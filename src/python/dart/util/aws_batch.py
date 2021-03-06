import re
from dart.model.action import Action
import logging

_logger = logging.getLogger(__name__)


class AWS_Batch_Dag(object):
    def __init__(self, config_metadata, client):
        self.job_definition = config_metadata(['aws_batch', 'job_definition']) # TODO: remove - we should use the action.data.engine_name value
        self.job_definition_suffix = config_metadata(['aws_batch', 'job_definition_suffix']) # to discern between prd/stg images
        self.job_queue = config_metadata(['aws_batch', 'job_queue'])
        _logger.info("AWS_Batch: using job_definition={0} and job_queue={1}".format(self.job_definition, self.job_queue))
        self.client = client

    def generate_job_name(self, workflow_id, order_idx, action_name):
        """ Names should not exceed 50 characters or else cloudwatch logs will fail. """
        job_name = "{0}_{1}_{2}".format(workflow_id, order_idx, action_name.replace("-", "_"))
        valid_characters_name = re.sub('[^a-zA-Z0-9_]', '', job_name) # job name valid pattern is: [^a-zA-Z0-9_]
        return valid_characters_name[0:50]

    def cancel_previous_jobs(self, previous_jobs, reason="Failed to create all jobs in a dag"):
        for idx, job_id in enumerate(previous_jobs):
            response = None
            try:
                response = self.client.cancel_job(jobId=job_id, reason=reason)
            except Exception as err:
                _logger.error("Failed to cancel job={0} {1}/{2} jobs. err:{3}".format(job_id, idx, len(previous_jobs), err))

            _logger.info("Canceling job #{0}={1}. metadata={2}".format(idx, job_id, response))

    def get_latest_active_job_definition(self, job_def_name):
        """ E.g.  job_def_name='redshift_engine' ==> we will get redshift_engine:3 (if 3 is the latest active version).
                  This will allow us to update the version used without changing the yaml files.
            Note: It is implicitly assumed that the AWS Batch job definitions are named the same as the engine names in dart.
        """
        job_name = "{0}_{1}".format(job_def_name, self.job_definition_suffix) # e.g. s3_engine_prd, or redshift_engine_stg
        _logger.info("AWS_batch: searching latest active job definition for name={0}".format(job_name))
        response = self.client.describe_job_definitions(jobDefinitionName=job_name, status='ACTIVE')
        _logger.info("AWS_batch: len(response['jobDefinitions'])={0}".format(len(response['jobDefinitions'])))
        arr = sorted(response['jobDefinitions'],
                     key=lambda x: int(x['revision']))  # the last item will be the highest (last) revision
        return arr[-1]['jobDefinitionArn']

    def generate_dag(self, ordered_actions, workflow_id):
        if not ordered_actions or not all(isinstance(x, Action) for x in ordered_actions):
            raise ValueError('Must receive actions in order to build a DAG. action={0}'.format(ordered_actions))

        _logger.info("AWS_Batch: generate_dag, number of actions = {0}".format(len(ordered_actions)))
        previous_jobs = [] # will hold an array of jobIds, one per each action placed in Batch
        for oaction in ordered_actions:
            cmd = ["echo", str(oaction.data.order_idx)]

            dependency = []
            if previous_jobs:
                dependency = [{'jobId': previous_jobs[-1]}]

            response = {'jobId': None}
            try:
                job_name = self.generate_job_name(workflow_id, oaction.data.order_idx, oaction.data.name)
                _logger.info("AWS_Batch: job-name={0}, dependsOn={1}, cmd={2}".format(job_name, dependency, cmd))

                response = self.client.submit_job(jobName=job_name,
                                                  jobDefinition=self.get_latest_active_job_definition('awscli'), # TODO: replace with oaction.data.engine_name
                                                  jobQueue=self.job_queue,
                                                  containerOverrides={'command': cmd})
                _logger.info("AWS_Batch: response={0}".format(response))

                previous_jobs.append(response['jobId'])
            except Exception as err:
                _logger.error("AWS_Batch: DAG-err={0}".format(err))
                self.cancel_previous_jobs(previous_jobs)
                raise

        _logger.info("AWS_Batch: Done building workflow {0}".format(workflow_id))

