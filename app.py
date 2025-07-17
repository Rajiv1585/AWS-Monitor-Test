from flask import Flask, render_template, request
import boto3
import humanize
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask_sqlalchemy import SQLAlchemy
import os

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'aws_dashboard.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class Instance(db.Model):
    InstanceId = db.Column(db.String, primary_key=True)
    Region = db.Column(db.String)
    State = db.Column(db.String)
    InstanceType = db.Column(db.String)
    AvailabilityZone = db.Column(db.String)
    PublicIpAddress = db.Column(db.String)
    PrivateIpAddress = db.Column(db.String)
    LaunchTime = db.Column(db.String)
    LaunchTimeHuman = db.Column(db.String)
    BlockDevices = db.relationship('BlockDevice', backref='instance', lazy=True)
    PatchStatus = db.Column(db.String)

class BlockDevice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    InstanceId = db.Column(db.String, db.ForeignKey('instance.InstanceId'))
    VolumeId = db.Column(db.String)
    DeviceName = db.Column(db.String)
    Size = db.Column(db.Integer)
    VolumeType = db.Column(db.String)

class EKSCluster(db.Model):
    Name = db.Column(db.String, primary_key=True)
    Status = db.Column(db.String)
    Version = db.Column(db.String)
    Endpoint = db.Column(db.String)
    VPC = db.Column(db.String)
    Region = db.Column(db.String)
    CreatedAt = db.Column(db.String)
    CreatedHuman = db.Column(db.String)

class RDSInstance(db.Model):
    Identifier = db.Column(db.String, primary_key=True)
    Engine = db.Column(db.String)
    EngineVersion = db.Column(db.String)
    LatestVersion = db.Column(db.String)
    UpdateAvailable = db.Column(db.Boolean)
    Status = db.Column(db.String)
    Class = db.Column(db.String)
    AllocatedStorage = db.Column(db.String)
    Region = db.Column(db.String)
    Endpoint = db.Column(db.String)
    Port = db.Column(db.String)
    CreatedAt = db.Column(db.String)
    CreatedHuman = db.Column(db.String)


with app.app_context():
    db.create_all()

def get_all_regions():
    ec2 = boto3.client('ec2')
    try:
        response = ec2.describe_regions(AllRegions=True)
        return [r['RegionName'] for r in response['Regions'] if r['OptInStatus'] in ('opt-in-not-required', 'opted-in')]
    except Exception as e:
        print(f"Failed to fetch regions: {e}")
        return []

def fetch_and_store_ec2_instances():
    Instance.query.delete()
    BlockDevice.query.delete()
    db.session.commit()

    for region in get_all_regions():
        try:
            ec2 = boto3.client('ec2', region_name=region)
            response = ec2.describe_instances()
            ssm = boto3.client('ssm', region_name=region)
            response = ec2.describe_instances()
            instance_ids = []

            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    inst = Instance(
                        InstanceId=instance['InstanceId'],
                        Region=region,
                        State=instance['State']['Name'],
                        InstanceType=instance['InstanceType'],
                        AvailabilityZone=instance['Placement']['AvailabilityZone'],
                        PublicIpAddress=instance.get('PublicIpAddress', 'N/A'),
                        PrivateIpAddress=instance.get('PrivateIpAddress', 'N/A'),
                        LaunchTime=instance['LaunchTime'].strftime('%Y-%m-%d %H:%M:%S UTC'),
                        LaunchTimeHuman=humanize.naturaltime(datetime.now(timezone.utc) - instance['LaunchTime'])
                    )
                    db.session.add(inst)

                    for bd in instance.get('BlockDeviceMappings', []):
                        ebs = bd.get('Ebs')
                        if ebs:
                            try:
                                vol = ec2.describe_volumes(VolumeIds=[ebs['VolumeId']])['Volumes'][0]
                                block = BlockDevice(
                                    InstanceId=instance['InstanceId'],
                                    VolumeId=ebs['VolumeId'],
                                    DeviceName=bd['DeviceName'],
                                    Size=vol['Size'],
                                    VolumeType=vol['VolumeType']
                                )
                            except:
                                block = BlockDevice(
                                    InstanceId=instance['InstanceId'],
                                    VolumeId=ebs['VolumeId'],
                                    DeviceName=bd['DeviceName'],
                                    Size=None,
                                    VolumeType='N/A'
                                )
                            db.session.add(block)
            db.session.commit()
            
            if instance_ids:
                try:
                    patch_states = ssm.describe_instance_patch_states(InstanceIds=instance_ids)['InstancePatchStates']
                    patch_map = {
                    p['InstanceId']: 'UP_TO_DATE' if p['MissingCount'] == 0 else 'UPDATE_AVAILABLE'
                    for p in patch_states
                    }

                    for inst_id, patch_status in patch_map.items():
                        instance = Instance.query.get(inst_id)
                        if instance:
                            instance.PatchStatus = patch_status
                    db.session.commit()
                except Exception as ssm_err:
                    print(f"SSM Patch info failed for {region}: {ssm_err}")

        except Exception as e:
            print(f"EC2 error in {region}: {e}")

            

def fetch_and_store_eks_clusters():
    EKSCluster.query.delete()
    db.session.commit()

    for region in get_all_regions():
        try:
            eks = boto3.client('eks', region_name=region)
            for name in eks.list_clusters()['clusters']:
                desc = eks.describe_cluster(name=name)['cluster']
                created = desc['createdAt'].replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
                cluster = EKSCluster(
                    Name=desc['name'],
                    Status=desc['status'],
                    Version=desc['version'],
                    Endpoint=desc['endpoint'],
                    VPC=desc['resourcesVpcConfig'].get('vpcId', 'N/A'),
                    Region=region,
                    CreatedAt=created.strftime('%Y-%m-%d %I:%M:%S %p %Z'),
                    CreatedHuman=humanize.naturaltime(datetime.now(ZoneInfo("Asia/Kolkata")) - created)
                )
                db.session.add(cluster)
            db.session.commit()
        except Exception as e:
            print(f"EKS error in {region}: {e}")

def fetch_and_store_rds_instances():
    RDSInstance.query.delete()
    db.session.commit()
    latest_versions = {}

    for region in get_all_regions():
        try:
            rds = boto3.client('rds', region_name=region)
            response = rds.describe_db_instances()
            for dbi in response['DBInstances']:
                created = dbi['InstanceCreateTime'].replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
                engine = dbi['Engine']
                current_version = dbi['EngineVersion']

                if engine not in latest_versions:
                    try:
                        latest_versions[engine] = rds.describe_db_engine_versions(Engine=engine, DefaultOnly=True)['DBEngineVersions'][0]['EngineVersion']
                    except:
                        latest_versions[engine] = current_version

                update_available = current_version != latest_versions[engine]

                rds_inst = RDSInstance(
                    Identifier=dbi['DBInstanceIdentifier'],
                    Engine=engine,
                    EngineVersion=current_version,
                    LatestVersion=latest_versions[engine],
                    UpdateAvailable=update_available,
                    Status=dbi['DBInstanceStatus'],
                    Class=dbi['DBInstanceClass'],
                    AllocatedStorage=str(dbi.get('AllocatedStorage', 'N/A')),
                    Region=region,
                    Endpoint=dbi.get('Endpoint', {}).get('Address', 'N/A'),
                    Port=str(dbi.get('Endpoint', {}).get('Port', 'N/A')),
                    CreatedAt=created.strftime('%Y-%m-%d %I:%M:%S %p %Z'),
                    CreatedHuman=humanize.naturaltime(datetime.now(ZoneInfo("Asia/Kolkata")) - created)
                )
                db.session.add(rds_inst)
            db.session.commit()
        except Exception as e:
            print(f"RDS error in {region}: {e}")

def get_distinct_field_values():
    return {
        'states': sorted([r[0] for r in db.session.query(Instance.State).distinct() if r[0]]),
        'types': sorted([r[0] for r in db.session.query(Instance.InstanceType).distinct() if r[0]]),
        'zones': sorted([r[0] for r in db.session.query(Instance.AvailabilityZone).distinct() if r[0]]),
        'regions': sorted([r[0] for r in db.session.query(Instance.Region).distinct() if r[0]]),
    }

@app.route('/')
def index():
    state = request.args.get('state')
    instances = Instance.query.filter_by(State=state).all() if state else Instance.query.all()
    field_options = get_distinct_field_values()
    return render_template(
        'index.html',
        instances=instances,
        request=request,
        field_options=field_options,
        current_filters={'state': state or ''}
    )


@app.route('/eks')
def eks():
    status = request.args.get('status') or ''
    version = request.args.get('version') or ''
    region = request.args.get('region') or ''

    query = EKSCluster.query
    if status:
        query = query.filter_by(Status=status)
    if version:
        query = query.filter_by(Version=version)
    if region:
        query = query.filter_by(Region=region)

    clusters = query.all()

    return render_template(
        'eks.html',
        clusters=clusters,
        request=request,
        current_filters={'status': status, 'version': version, 'region': region}
    )

@app.route('/rds')
def rds():
    engine = request.args.get('engine') or ''
    status = request.args.get('status') or ''
    region = request.args.get('region') or ''

    query = RDSInstance.query
    if engine:
        query = query.filter_by(Engine=engine)
    if status:
        query = query.filter_by(Status=status)
    if region:
        query = query.filter_by(Region=region)

    return render_template(
        'rds.html',
        rds_list=query.all(),
        request=request,
        current_filters={
            'engine': engine,
            'status': status,
            'region': region
        }
    )

@app.route('/refresh')
def refresh():
    fetch_and_store_ec2_instances()
    return "Refreshed EC2 instances."

@app.route('/eks/refresh')
def eks_refresh():
    fetch_and_store_eks_clusters()
    return "Refreshed EKS clusters."

@app.route('/rds/refresh')
def rds_refresh():
    fetch_and_store_rds_instances()
    return "Refreshed RDS instances."

if __name__ == '__main__':
    app.run(debug=True)
