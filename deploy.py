import os
import boto3
from botocore.exceptions import ClientError
from datetime import datetime

def create_keypair(name="YazabiServerKeypair"):
    """
    Creates a keypair in AWS. If name is not provided, it defaults to
    YazabiServerKeypair for the name
    """
    print('Creating AWS keypair ...')
    ec2 = boto3.client('ec2')
    try:
        response = ec2.create_key_pair(KeyName=name)
    except ClientError as ex:
        if "InvalidKeyPair.Duplicate" in ex.args[0]:
            print('The keypair already exists, modifying keypair name ...')
            timestamp = datetime.now().strftime('%Y-%m-%d_%H:%M:%S')
            name = '{}_{}'.format(name, timestamp)
            response = ec2.create_key_pair(KeyName=name)
    keymaterial = response['KeyMaterial']
    keyfilename = '{}.pem'.format(name)
    print("Created keypair with name {}".format(name))
    print('Writing keypair to {} in current directory'.format(keyfilename))
    with open(keyfilename, 'w') as kfile:
        kfile.write(keymaterial)
    # Make file read-only by user for security reasons. Your SSH client will also reject
    # the private key file if it doesn't have correct permissions.
    os.chmod(keyfilename, 0o600)
    return name

def deploy_template(keyname):
    """
    Deploys the template file using aws cloudformation
    """
    cf = boto3.client('cloudformation')
    with open('file.yaml') as template:
        body = template.read()
    stackname = "YazabiServerStack"
    try:
        response = cf.create_stack(
            StackName=stackname,
            TemplateBody=body,
            Parameters=[
                {
                    'ParameterKey': "KeyName",
                    'ParameterValue': keyname
                }
            ],
            OnFailure="ROLLBACK")
    except ClientError:
        print('A stack with name ({}) already exists, appending current'
              ' timestamp to name ...'.format(stackname))
        timestamp = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        stackname = '{}-{}'.format(stackname, timestamp)
        print('Creating stack with name {} ...'.format(stackname))
        response = cf.create_stack(
            StackName=stackname,
            TemplateBody=body,
            Parameters=[
                {
                    'ParameterKey': "KeyName",
                    'ParameterValue': keyname
                }
            ],
            OnFailure="ROLLBACK")
    print('Waiting for stack creation to complete ...')
    create_complete_waiter = cf.get_waiter('stack_create_complete')
    create_complete_waiter.wait(StackName=stackname)
    stack_info = cf.describe_stacks(StackName=stackname)
    outputs = stack_info['Stacks'][0]['Outputs']
    ip_addr = None
    for output in outputs:
        if output['OutputKey'] == "InstanceIPAddress":
            ip_addr = output['OutputValue']
    if ip_addr is not None:
        print('Floating IP of the instance is: {}'.format(ip_addr))
        print('SSH into your instance with: ')
        print('    ssh -i {}.pem ubuntu@{}'.format(keyname, ip_addr))
    else:
        raise ValueError('Did not receive an IP address')
    return stack_info

def main():
    # We need to create the SSH keypair first because AWS CloudFormation doesn't
    # support creating keypairs. We create it within AWS so we need not assume
    # anything about existing key generation tools
    keyname = create_keypair()
    # Now we need to deploy the template.
    response = deploy_template(keyname)



if __name__ == "__main__":
    main()
