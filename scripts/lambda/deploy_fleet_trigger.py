"""
Fleet Management Platform - CodeCommit Deploy Trigger
Lambda function that executes deployment on EC2 when CodeCommit receives push
"""

import json
import boto3
import paramiko
import logging
from io import StringIO

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
secrets_manager = boto3.client('secretsmanager')

# Configuration
SECRET_NAME = 'fleet-ssh-key'
EC2_HOST = '34.236.90.56'  # Public IP (Lambda not in VPC)
EC2_USER = 'ubuntu'
DEPLOY_SCRIPT_PATH = '/home/ubuntu/ss-fleet-core/scripts/deploy.sh'

def get_ssh_key():
    """Retrieve SSH private key from AWS Secrets Manager"""
    try:
        response = secrets_manager.get_secret_value(SecretId=SECRET_NAME)
        secret = json.loads(response['SecretString'])
        return secret['ssh_key']
    except Exception as e:
        logger.error(f"Failed to retrieve SSH key from Secrets Manager: {str(e)}")
        raise


def execute_remote_command(ssh_client, command, timeout=300):
    """Execute command on remote server via SSH"""
    try:
        logger.info(f"Executing command: {command}")
        stdin, stdout, stderr = ssh_client.exec_command(command, timeout=timeout)

        # Read output with timeout
        output = stdout.read().decode('utf-8')
        error = stderr.read().decode('utf-8')
        exit_code = stdout.channel.recv_exit_status()

        if exit_code != 0:
            logger.error(f"Command failed with exit code {exit_code}")
            logger.error(f"Error output: {error}")
            return False, error

        logger.info(f"Command output: {output}")
        return True, output

    except Exception as e:
        logger.error(f"Failed to execute command: {str(e)}")
        return False, str(e)


def lambda_handler(event, context):
    """
    Lambda handler triggered by CodeCommit push event

    Args:
        event: CodeCommit event data
        context: Lambda context

    Returns:
        dict: Response with status code and message
    """

    logger.info("Fleet deployment Lambda triggered")
    logger.info(f"Event: {json.dumps(event)}")

    # Parse CodeCommit event
    try:
        records = event.get('Records', [])
        if not records:
            logger.warning("No records found in event")
            return {
                'statusCode': 200,
                'body': json.dumps('No changes to deploy')
            }

        # Get repository and branch information
        for record in records:
            event_name = record.get('eventName', '')

            if 'codecommit' not in event_name.lower():
                logger.info(f"Skipping non-CodeCommit event: {event_name}")
                continue

            # Extract branch information
            references = record.get('codecommit', {}).get('references', [])

            # Only deploy on main branch pushes
            main_branch_updated = any(
                ref.get('ref', '').endswith('/main')
                for ref in references
            )

            if not main_branch_updated:
                logger.info("Push not to main branch, skipping deployment")
                return {
                    'statusCode': 200,
                    'body': json.dumps('Only main branch triggers deployment')
                }

    except Exception as e:
        logger.error(f"Failed to parse CodeCommit event: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Event parsing failed: {str(e)}')
        }

    # Retrieve SSH key
    try:
        ssh_key_content = get_ssh_key()
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps(f'Failed to retrieve SSH key: {str(e)}')
        }

    # Connect to EC2 via SSH
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        # Load private key
        key_file = StringIO(ssh_key_content)
        private_key = paramiko.RSAKey.from_private_key(key_file)

        logger.info(f"Connecting to {EC2_HOST} as {EC2_USER}")
        ssh_client.connect(
            hostname=EC2_HOST,
            username=EC2_USER,
            pkey=private_key,
            timeout=30,  # 30 seconds for connection
            banner_timeout=30
        )

        logger.info("SSH connection established")

        # Pull latest code from repository
        logger.info("Pulling latest code from CodeCommit")

        # Reset to clean state and pull latest code
        success, output = execute_remote_command(
            ssh_client,
            "cd /home/ubuntu/ss-fleet-core && "
            "sudo git reset --hard HEAD && "
            "sudo git pull origin main && "
            "sudo chown -R ubuntu:ubuntu .",
            timeout=60
        )

        if not success:
            logger.error(f"Git pull failed: {output}")
            return {
                'statusCode': 500,
                'body': json.dumps(f'Git pull failed: {output}')
            }

        logger.info(f"Git pull successful: {output}")

        # Make deploy script executable
        success, output = execute_remote_command(
            ssh_client,
            f"chmod +x {DEPLOY_SCRIPT_PATH}"
        )

        if not success:
            return {
                'statusCode': 500,
                'body': json.dumps(f'Failed to make deploy script executable: {output}')
            }

        # Execute deployment script with sudo for docker access
        success, output = execute_remote_command(
            ssh_client,
            f"sudo bash {DEPLOY_SCRIPT_PATH}",
            timeout=300
        )

        if not success:
            return {
                'statusCode': 500,
                'body': json.dumps(f'Deployment failed: {output}')
            }

        logger.info("Deployment completed successfully")

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Deployment successful',
                'output': output
            })
        }

    except Exception as e:
        logger.error(f"Deployment error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Deployment error: {str(e)}')
        }

    finally:
        ssh_client.close()
        logger.info("SSH connection closed")
