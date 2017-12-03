import boto3, netaddr, random, string, requests, json

from netaddr import IPSet, IPNetwork
from boto3.dynamodb.conditions import Key, Attr

debug = ''

def sendresponse(event, context, responsestatus, responsedata, reason):
    """Send a Success or Failure event back to CFN stack"""
    payload = {
        'StackId': event['StackId'],
        'Status' : responsestatus,
        'Reason' : reason,
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'PhysicalResourceId': event['LogicalResourceId'] + \
            ''.join(random.SystemRandom().choice(string.ascii_uppercase + string.digits) \
            for _ in range(10)),
        'Data': responsedata
    }
    if debug == "True":
        print "Sending %s to %s" % (json.dumps(payload), event['ResponseURL'])
    requests.put(event['ResponseURL'], data=json.dumps(payload))
    print "Sent %s to %s" % (json.dumps(payload), event['ResponseURL'])



def usesubnets(cidr, ddb_t, stack_id):
    """ Add the passed subnet into the DB"""
    ddb_t.put_item(Item={
        'Cidr': str(cidr),
        'StackId': str(stack_id),
    })
    return str(cidr)

def deletesubnets(stack_id, ddb_t):
    """ Delete any subnets in the DB for a stack"""
    try:
        response = ddb_t.scan(
            FilterExpression=Attr('StackId').eq(stack_id)
        )
        for item in response['Items']:
            print item
            ddb_t.delete_item(Key={'Cidr' : item['Cidr']})
    except Exception:
        pass

def is_cidr_in_table(cidr, ddb_t):
    """ Check if the CIDR is already allocated """
    cidr = str(cidr)
    response = ddb_t.query(
        KeyConditionExpression=Key('Cidr').eq(cidr)
    )
    items = response['Items']
    if len(items) > 0:
        return True
    return False

def is_cidr_reserved(cidr, vpccidr):
    """ Check if CIDR is in the first 2 x /22 in the VPC Range """
    cidr = str(cidr)
    vpc = IPNetwork(vpccidr)
    reserved1 = list(vpc.subnet(22))[0]
    reserved2 = list(vpc.subnet(22))[1]

    if reserved1 == IPNetwork(cidr).supernet(22)[0] or reserved2 == IPNetwork(cidr).supernet(22)[0]:
        return True
    return False

def handler(event, context):
    """ Attempt to allocate 4 IP ranges from a VPC CIDR Block"""

    print "Started execution of Autosubnet Lambda..."
    print "Function ARN %s" % context.invoked_function_arn
    print "Incoming Event %s " % json.dumps(event)
    global debug

    tablename = ''
    stack_id = ''
    region = ''
    ddb_t = None
    vpccidr = ''
    requesttype = ''

    try: # attempt to set debug status from CFN config - otherwise true
        debug = event['ResourceProperties']['debug']
    except Exception:
        debug = "False"

    if debug == "True": # Print context and event - only if debug
        print event
        print context

    try: # get the stack ID from incoming event, if not-present FAIL
        stack_id = event['StackId']
    except Exception:
        sendresponse(event, context, 'FAILED', {'Error': 'Cant determine stackid'}, "Cant determine stackid")
        return

    try: # Check that DynamoDB region has been provided by template, if not, FAIL
        region = event['ResourceProperties']['DynamoDBRegion']
    except Exception:
        sendresponse(event, context, 'FAILED', {'Error': 'Region not provided.'}, "Region not provided.")
        return

    try: # Check that DynamoDB tablename has been provided by template, if not, FAIL
        tablename = event['ResourceProperties']['DynamoDBTable']
    except Exception:
        sendresponse(event, context, 'FAILED', {'Error': 'Tablename not provided.'}, "Tablename not provided.")
        return

    try: # Check that sharedinfrastructure VPCCidr has been provided by template, if not, FAIL
        vpccidr = event['ResourceProperties']['VPCCidr']
    except Exception:
        sendresponse(event, context, 'FAILED', {'Error': 'VPC Cidr not provided.'}, "VPC Cidr not provided.")
        return

    try: # Check that we can determine request type ... interested in CREATE or DELETE
        requesttype = event['RequestType']
    except Exception:
        sendresponse(event, context, 'FAILED', {'Error': 'Cant determine request type.'}, "Cant determine request type.")

    if debug == "True":
        print "Past Input Checking"

    # make sure we have all the required inputs to the function
    # If function is still executing - we're all good on inputs
    # Connect to the DDB Table
    ddb_t = boto3.resource('dynamodb', region_name=region).Table(tablename)

    if requesttype == 'Delete': # if delete, remove the subnet allocations, for this stack_id
        if debug == 'True':
            print "Delete Requesttype Processing Started"
        deletesubnets(stack_id, ddb_t)
        sendresponse(event, context, 'SUCCESS', {}, "")
        return

    ## otherwise allocate 4 ranges
    if debug == "True":
        print "Create Requesttype Processing Started"
    subnetmask = 24 # size of networks to allocate - static for now
    ipnet = IPNetwork(vpccidr)
    subnetsallocated = 0

    # Save possible client address ranges to an array
    subnets = ipnet.subnet(subnetmask)
    success = False

    if debug == "True":
        print "Starting Possible Subnet Iteration"
    for subnet in subnets:
        if subnetsallocated == 4:
            success = True
            break
        if is_cidr_reserved(subnet, vpccidr):
            continue
        if is_cidr_in_table(subnet, ddb_t):
            continue
        subnetsallocated += 1
        print "Subnets Allocated %d - %s" % (subnetsallocated, str(subnet))
        usesubnets(subnet, ddb_t, stack_id) # store the allocation in the DB

    responsedata = {} # dictionary to store our return data
    # read all the subnets for this stack from table
    response = ddb_t.query(
        IndexName='rangesforstack',
        KeyConditionExpression=Key('StackId').eq(stack_id)
    )
    if len(response['Items']) < 4:
        success = False
        sendresponse(event, context, 'FAILED', responsedata, "Cant read 4 CIDRs for stack from DDB")
        exit(1)
    if debug == "True":
        for item in response['Items']:
            print item
    if success:
        responsedata = {
            'AppPublicCIDRA' : response['Items'][0]['Cidr'],
            'AppPublicCIDRB' : response['Items'][1]['Cidr'],
            'AppPrivateCIDRA' : response['Items'][2]['Cidr'],
            'AppPrivateCIDRB' : response['Items'][3]['Cidr']
        }
        sendresponse(event, context, 'SUCCESS', responsedata, "N/A")
    else:
        sendresponse(event, context, 'FAILED', responsedata, "Create Failed .. Misc")
