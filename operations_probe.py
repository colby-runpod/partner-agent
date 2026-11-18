"""External readiness probe. Deliberately has no message-delivery permissions."""
import json
import os
import urllib.error
import urllib.request


def probe(url, opener=urllib.request.urlopen):
    try:
        with opener(url, timeout=15) as response:
            data=json.load(response)
            return response.status == 200 and data.get('ready') is True
    except (OSError, ValueError, urllib.error.URLError):
        return False


def handler(event, context):
    import boto3
    ready=probe(os.environ['READINESS_URL'])
    boto3.client('cloudwatch').put_metric_data(
        Namespace='Runpod/PartnerAgent',
        MetricData=[{'MetricName':'Ready','Value':int(ready),'Unit':'Count',
                     'Dimensions':[{'Name':'Service','Value':os.environ['AGENT_SERVICE']}]}])
    return {'ready':ready}
