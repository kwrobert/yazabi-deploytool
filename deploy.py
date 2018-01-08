import argparse as ap
from datetime import datetime
from botocore.exceptions import ClientError, WaiterError
from itertools import zip_longest
import os
import boto3
import paramiko

class StackError(Exception):
    """
    An exception to raise when the stack creation fails
    """
    
    def __init__(self, op, resp):
        msg = self.build_msg(op, resp)
        super(StackError, self).__init__(msg)

    def build_msg(self, op, resp):
        stack_events = resp['StackEvents']
        errs = [event for event in stack_events if event['ResourceStatus'] ==
                'CREATE_FAILED']
        msg_body = "The following resources failed during stack {}:\n\n"
        msg_body.format(op)
        for err in errs:
            entry = "Resource Type: {}\nResource ID: {}\nReason: {}\n".format(
                err['ResourceType'], err['LogicalResourceId'], err['ResourceStatusReason'])
            msg_body += entry
        return msg_body

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

def deploy_template(keyname, stackname, vsize, inst_type):
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
                },
                {
                    'ParameterKey': "VolumeSize",
                    'ParameterValue': vsize
                },
                {
                    'ParameterKey': "InstanceType",
                    'ParameterValue': inst_type
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
    try:
        create_complete_waiter.wait(StackName=stackname)
    except WaiterError:
        resp = cf.describe_stack_events(StackName=stackname)
        raise StackError("create", resp)

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
    cudnn_path = ''
    while not os.path.isfile(cudnn_path):
        cudnn_path = input('Please enter the path to the cuDNN tar file '
                           '[./cudnn-8.0-linux-x64-v6.0.tgz]: ')
        if not cudnn_path:
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
    for oline, eline in zip_longest(iter(stdout.readline, ""),
                                    iter(stderr.readline, "")):
        if oline is not None:
            print(oline, end="")
        if eline is not None:
            print(eline, end="")
    stdin.close()
    stdout.close()
    stderr.close()
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
    server_ip = deploy_template(keyname, args.stackname, args.volume, 
                                args.instance)
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

def _choose_stack(cf, action="delete"):
    """
    Reusable function that allows the user to pick a particular stack out of a
    list of deployed stacks
    """

    resp = cf.describe_stacks()
    names = [stack['StackName'] for stack in resp['Stacks']]
    if not names:
        print('There are no deployed stacks!')
        quit()
    msg_list = ""
    for i, name in enumerate(names):
        msg_list += "{}. {}\n".format(i+1, name)
    msg = "Here are the stacks you have deployed:\n"+msg_list
    msg += "Please choose the number of the stack you wish to {}: ".format(action)
    stack_num = None
    while not isinstance(stack_num, int) or stack_num == 0:
        try:
            stack_num = int(input(msg))
        except ValueError:
            continue
    stackname = names[stack_num-1]
    return stackname

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
        stackname = _choose_stack(cf)
    info = cf.describe_stacks(StackName=stackname)['Stacks'][0]
    keyname = ""
    for par in info['Parameters']:
        if par['ParameterKey'] == 'KeyName':
            keyname = par['ParameterValue']
    if keyname:
        print('Deleting keypair: {}'.format(keyname))
        ec2.delete_key_pair(KeyName=keyname)
    print('Deleting stack: {}'.format(stackname))
    cf.delete_stack(StackName=stackname)
    delete_complete_waiter = cf.get_waiter('stack_delete_complete')
    try:
        delete_complete_waiter.wait(StackName=stackname)
    except WaiterError:
        resp = cf.describe_stack_events(StackName=stackname)
        raise StackError("delete", resp)
    print('Deletion complete!')

def stop(args):
    """
    Pause the instance within a stack, without destroying it, to preserve data
    but save on CPU hours
    """

    cf = boto3.client('cloudformation')
    ec2 = boto3.client('ec2')
    if args.stackname is not None:
        stackname = args.stackname
    else:
        stackname = _choose_stack(cf, action="stop")
    info = cf.list_stack_resources(StackName=stackname)
    inst_id = None
    for resource in info['StackResourceSummaries']:
        if resource['ResourceType'] == 'AWS::EC2::Instance':
            inst_id = resource['PhysicalResourceId']

    if inst_id:
        ec2.stop_instances(InstanceIds=[inst_id])
        stop_complete_waiter = ec2.get_waiter('instance_stopped')
        stop_complete_waiter.wait(InstanceIds=[inst_id])
        print('Instance successfully stopped!')
    else:
        print("Unable to stop instance, could not find running instances in"
              " specified stack")
    return None

def start(args):
    """
    Resume the instance within a stack, without destroying it, to preserve data
    but save on CPU hours
    """

    cf = boto3.client('cloudformation')
    ec2 = boto3.client('ec2')
    if args.stackname is not None:
        stackname = args.stackname
    else:
        stackname = _choose_stack(cf, action="start")
    info = cf.list_stack_resources(StackName=stackname)
    inst_id = None
    for resource in info['StackResourceSummaries']:
        if resource['ResourceType'] == 'AWS::EC2::Instance':
            inst_id = resource['PhysicalResourceId']

    if inst_id:
        ec2.start_instances(InstanceIds=[inst_id])
        start_complete_waiter = ec2.get_waiter('instance_running')
        start_complete_waiter.wait(InstanceIds=[inst_id])
        print('Instance successfully started!')
    else:
        print("Unable to start instance, could not find stopped instances in"
              " specified stack")

    return None

def main():
    parser = ap.ArgumentParser(description="""A tool for deploying a single
    server, backed by a persistent EBS volume and with a public Elastic IP
    address, to AWS automatically via CloudFormation. This tool will also run a
    bootstrap script to get you up and running with Tensorflow on Python3""")
    subparsers = parser.add_subparsers(title="subcommands", metavar='')
    parser_deploy = subparsers.add_parser("deploy", help="Deploy the stack")
    parser_deploy.add_argument('--stackname', default="YazabiServerStack",
                               help="""The name of the stack to deploy""")
    parser_deploy.add_argument('--keyname', default="YazabiServerKeypair",
                               help="""The name of the keypair to deploy""")
    parser_deploy.add_argument('--instance', type=str, default="p2.xlarge",
                               help="""The type of instance to deploy""")
    parser_deploy.add_argument('--volume', type=str, default="25",
                               help="""The size in GB of the volume to attach
                               to the instance""")
    parser_deploy.set_defaults(func=deploy)
    parser_delete = subparsers.add_parser("delete", help="Delete a stack")
    parser_delete.add_argument('--stackname', help="""The name of the stack to
    delete""")
    parser_delete.set_defaults(func=delete)
    parser_stop = subparsers.add_parser("stop", help="""Stop the instance in a
    stack""")
    parser_stop.add_argument('--stackname', help="""The name of the stack
    containing the instance to stop""")
    parser_stop.set_defaults(func=stop)
    parser_start = subparsers.add_parser("start", help="""Resume the instance in a
    stack""")
    parser_start.add_argument('--stackname', help="""The name of the stack
    containing the instance to start""")
    parser_start.set_defaults(func=start)
    args = parser.parse_args()
    print(args)
    quit()
    args.func(args)


if __name__ == "__main__":
    main()
