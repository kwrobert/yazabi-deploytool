import argparse as ap
from datetime import datetime
from botocore.exceptions import ClientError
from itertools import zip_longest
import os
import boto3
import paramiko

def create_keypair(name):
    """
    Creates a keypair with the given name in AWS. 
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

def deploy_template(keyname, stackname):
    """
    Deploys the template file using aws cloudformation
    """

    cf = boto3.client('cloudformation')
    tpath = 'templates/server_elasticIP_ebsrootvol.yaml'
    with open(tpath) as template:
        body = template.read()
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
    """
    Opens a paramiko ssh connection and uses sftp to upload the gzipped tar
    file of the cuDNN libraries we need. Unfortunately this step is a bit
    manual because the cuDNN libraries are hidden behind a login wall and I
    couldn't find a way to pull them down automatically
    """

    print('We need to upload the cuDNN libraries you downloaded!')
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(ip, username='ubuntu', key_filename=keyfile)
    sftp = ssh.open_sftp()
    cudnn_path = input('Please enter the path to the cuDNN tar file '
                       '[./cudnn-8.0-linux-x64-v6.0.tgz]: ')
    if not cudnn_path:
        print('no entry')
        cudnn_path = os.path.join(os.getcwd(), 'cudnn-8.0-linux-x64-v6.0.tgz')
    remotepath = '/home/ubuntu/cudnn-8.0-linux-x64-v6.0.tgz'
    print('Beginning upload ...')
    sftp.put(cudnn_path, remotepath, callback=printTotals)
    print('Upload complete!')
    sftp.close()
    return ssh

def run_bootstrap(client):
    """
    Uploads the bootstrap script to the server and executes it. Expects a
    connected paramiko SSH client as an argument.
    """

    sftp = client.open_sftp()
    print('Uploading bootstrap script ...')
    sftp.put('./bootstrap.sh', '/home/ubuntu/bootstrap.sh', callback=printTotals)
    print('Upload complete!')
    sftp.close()
    print("Executing bootstrap script ...")
    stdin, stdout, stderr = client.exec_command("sudo bash bootstrap.sh")
    stdin.close()
    for oline, eline in zip_longest(iter(stdout.readline, ""),
                                    iter(stderr.readline, "")):
        if oline is not None:
            print(oline, end="")
        if eline is not None:
            print(eline, end="")
    # for line in iter(stdout.readline, ""):
    # for line in iter(stderr.readline, ""):
    #     print(line, end="")
    print('\nBootstrapping complete!\n')
    print('Instance is currently rebooting, access may be down for a few'
          ' minutes')

def deploy(args):
    """
    Wrapper function to tie together all the steps for deployment
    """
    # We need to create the SSH keypair first because AWS CloudFormation doesn't
    # support creating keypairs. We create it within AWS so we need not assume
    # anything about existing key generation tools
    keyname = create_keypair(args.keyname)
    # Now we need to deploy the template.
    server_ip = deploy_template(keyname, args.stackname)
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

def delete(args):
    """
    Wrapper function to delete a stack. The name of the stack to delete is
    optional. Otherwise, this function will collect a list of deployed stacks
    and present them to the user, allowing them to choose which stack to delete
    or to cancel the operation
    """
    cf = boto3.client('cloudformation')
    ec2 = boto3.client('ec2')
    if args.stackname is not None:
        stackname = args.stackname
    else:
        resp = cf.describe_stacks()
        print(resp['Stacks'])
        names = [stack['StackName'] for stack in resp['Stacks']]
        if not names:
            print('There are no deployed stacks!')
            return
        msg_list = ""
        for i, name in enumerate(names):
            msg_list += "{}. {}\n".format(i+1, name)
        msg = "Here are the stacks you have deployed:\n"+msg_list
        msg += "Please choose the number of the stack you wish to delete: "
        stack_num = None
        while not isinstance(stack_num, int) or stack_num == 0:
            try:
                stack_num = int(input(msg))
            except ValueError:
                continue
        stackname = names[stack_num-1]
    info = cf.describe_stacks(StackName=args.stackname)
    keyname = ""
    for par in info['Parameters']:
        if par['ParameterKey'] == 'KeyName':
            keyname = par['ParameterValue']
    if keyname:
        print('Deleting keypair {}'.format(keyname))
        ec2.delete_key_pair(KeyName=keyname)
    print('Deleting stack {}'.format(stackname))
    cf.delete_stack(StackName=names[stackname])
    delete_complete_waiter = cf.get_waiter('stack_delete_complete')
    delete_complete_waiter.wait(StackName=stackname)
    print('Deletion complete!')

def main():
    parser = ap.ArgumentParser(description="""A tool for deploying a single
    server, backed by a persistent EBS volume and with a public Elastic IP
    address to AWS automatically via CloudFormation. This tool will also run a
    bootstrap script to get you up and running with Tensorflow on Python3""")
    subparsers = parser.add_subparsers(dest="subparser_name", help="[sub-command] help]")
    parser_deploy = subparsers.add_parser("deploy", help="Deploy the stack")
    parser_deploy.add_argument('--stackname', default="YazabiServerStack",
                               help="""The name of the stack to deploy""")
    parser_deploy.add_argument('--keyname', default="YazabiServerKeypair",
                               help="""The name of the keypair to deploy""")
    parser_deploy.set_defaults(func=deploy)
    parser_delete = subparsers.add_parser("delete", help="Delete a stack")
    parser_delete.add_argument('--stackname', help="""The name of the stack to
    delete""")
    parser_delete.set_defaults(func=delete)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
