import os
import boto3
import paramiko
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
        if ex.response['Error']['Code'] == 'InvalidKeyPair.Duplicate':
            print('The keypair already exists, modifying keypair name ...')
            timestamp = datetime.now().strftime('%Y-%m-%d_%H:%M:%S')
            name = '{}_{}'.format(name, timestamp)
            response = ec2.create_key_pair(KeyName=name)
        else:
            raise
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
    tpath = 'templates/server_elasticIP_ebsrootvol.yaml'
    with open(tpath) as template:
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
    except ClientError as ex:
        if ex.response['Error']['Code'] == 'AlreadyExistsException':
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
        else:
            raise
    print('Waiting for stack creation to complete ...')
    create_complete_waiter = cf.get_waiter('stack_create_complete')
    create_complete_waiter.wait(StackName=stackname)
    stack_info = cf.describe_stacks(StackName=stackname)
    outputs = stack_info['Stacks'][0]['Outputs']
    ip_addr = None
    for output in outputs:
        if output['OutputKey'] == "InstanceIPAddress":
            ip_addr = output['OutputValue']
    return ip_addr

def printTotals(transferred, toBeTransferred):
    print("Transferred: {:.2f} MB\tOut of: {:.2f} MB".format(transferred/(1000*1000),
                                                 toBeTransferred/(1000*1000)),
          end='\r')

def upload_cudnn(ip, keyfile):
    print('We need to upload the cuDNN libraries you downloaded!')
    ssh = paramiko.SSHClient() 
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(ip, username='ubuntu', key_filename=keyfile)
    sftp = ssh.open_sftp()
    cudnn_path = input('Please enter the path to the cuDNN tar file '
                       '[./cudnn-8.0-linux-x64-v6.0.tgz]: ')
    if not cudnn_path:
        print('no entry')
        cudnn_path = os.path.join(os.getcwd(),'cudnn-8.0-linux-x64-v6.0.tgz')
    remotepath = '/home/ubuntu/cudnn-8.0-linux-x64-v6.0.tgz'
    print('Beginning upload ...')
    sftp.put(cudnn_path, remotepath, callback=printTotals)
    print('Upload complete!')
    sftp.close()
    return ssh

def run_bootstrap(client):
    sftp = client.open_sftp()
    print('Uploading bootstrap script ...')
    sftp.put('./bootstrap.sh', '/home/ubuntu/bootstrap.sh', callback=printTotals)
    print('Upload complete!')
    sftp.close()
    print("Executing bootstrap script ...")
    stdin, stdout, stderr = client.exec_command("sudo bash bootstrap.sh")
    stdin.close()
    for line in iter(stdout.readline, ""):
        print(line, end="")

def main():
    # We need to create the SSH keypair first because AWS CloudFormation doesn't
    # support creating keypairs. We create it within AWS so we need not assume
    # anything about existing key generation tools
    keyname = create_keypair()
    # Now we need to deploy the template.
    server_ip = deploy_template(keyname)
    # server_ip = '18.220.30.107'
    # keyname = "YazabiServerKeypair"
    print(server_ip)
    print(keyname)
    # Copy up the cuDNN libs
    ssh_client = upload_cudnn(server_ip, '{}.pem'.format(keyname))
    # Finally, run the bootstrap script
    run_bootstrap(ssh_client)
    if server_ip is not None:
        print('Floating IP of the instance is: {}'.format(server_ip))
        print('SSH into your instance with: ')
        print('    ssh -i {}.pem ubuntu@{}'.format(keyname, server_ip))
    else:
        raise ValueError('Did not receive an IP address')


if __name__ == "__main__":
    main()
