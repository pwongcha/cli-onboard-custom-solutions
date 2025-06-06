from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from copy import deepcopy
from shutil import which
from time import gmtime
from time import strftime
from urllib import parse

import pandas as pd
from cerberus import Validator
from distutils.dir_util import copy_tree
from exceptions import get_cli_root_directory
from exceptions import setup_logger
from onboard_custom import Onboard
from pyisemail import is_email
from rich import print_json
from tabulate import tabulate
from wrapper_api import apiCallsWrapper

logger = setup_logger()
root = get_cli_root_directory()

space = ' '
column_width = 50


class utility:
    def __init__(self):
        """
        Function to initialize a common status indicator,
        This variable should be updated by every function
        defined in validation modules to indicate validation status.
        This avoid usage of too many IF Conditions.
        """
        # Initialize the variable to true
        self.valid = True
        self.validate_prerequisite_cli()
        self.start_time = time.perf_counter()

    def installedCommandCheck(self, command_name) -> bool:
        """
        Function to check installation of a command.
        """
        if which(command_name) is None:
            self.valid = False
            logger.error(f'This program needs {command_name} as a pre-requisite')
            if command_name == 'akamai':
                logger.warning('Please install from https://github.com/akamai/cli')
            else:
                logger.error(f'{command_name} is not installed')

        return self.valid

    def executeCommand(self, command) -> bool:
        """
        Function to execute Linux commands
        """
        childprocess = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        stdout, stderr = childprocess.communicate()
        if 'pipeline' in command and 'akamai [global flags]' in str(stdout):
            self.valid = False
            print()
            logger.error('This program needs akamai CLI module property-manager as a pre-requisite')
            logger.warning('Please install from https://github.com/akamai/cli-property-manager')
            logger.warning('or run >> akamai install property-manager')

        if 'cloudlets' in command and 'akamai [global flags]' in str(stdout):
            self.valid = False
            print()
            logger.error('This program needs akamai CLI module cloudlets as a pre-requisite')
            logger.warning('Please install from https://github.com/akamai/cli-cloudlets')
            logger.warning('or run >> akamai install cloudlets')

        return self.valid

    def checkPermissions(self, session, apicalls_wrapper_object):
        """
        Function to check credentials permissions required
        """
        # This function is not used. Helpful in future if we want to check permissions of credential
        credential_details_response = apicalls_wrapper_object.checkAuthorization(session)
        print(json.dumps(credential_details_response.json(), indent=4))
        if credential_details_response.status_code == 200:
            for scope in credential_details_response.json()['scope'].split(' '):
                o = parse.urlparse(scope)
                apis = o.path.split('/')
                print(f'{apis[3]:35} {apis[5]:10}')
        else:
            pass
        # Default Return, ideally code shouldnt come here
        return self.valid

    def validateSetupStepsCSV(self, onboard_object, wrapper_object, cli_mode='batch-create') -> bool:
        """
        Function to validate the input values of setup.json when in batch-create mode
        """

        count = 0
        valid_waf = True
        print()
        logger.warning('Validating setup file information. Please wait, may take a few moments')

        # check if csv is valid
        if not onboard_object.valid_csv:
            logger.error(f'{onboard_object.csv_loc:<30}{space:>20}invalid csv; check above validation errors')
            count += 1

        # check if property name exists
        for property in onboard_object.property_name:
            width = column_width - len(property)
            msg = f'{property}{space:>{width}}'
            if wrapper_object.property_exists(property):
                logger.error(f'{msg}invalid property name; already in use')
                count += 1
            else:
                logger.info(f'{msg}valid property name')

        # if activating pm to prod, must active to staging first
        if onboard_object.activate_property_production:
            if onboard_object.activate_property_staging is not True:
                logger.error('Must activate property to STAGING before activating to PRODUCTION')
                count += 1

        # must activate waf config to staging before activating waf to prodution
        if onboard_object.activate_waf_policy_production:
            if not onboard_object.activate_waf_policy_staging:
                logger.error('Must activate WAF policy to STAGING before activating to PRODUCTION.')
                count += 1

        # validate product id available per contract
        product_detail = self.validateProductId(wrapper_object,
                                                onboard_object.contract_id,
                                                onboard_object.product_id)
        if product_detail['Found']:
            logger.info(f'{onboard_object.product_id}{space:>{column_width - len(onboard_object.product_id)}}valid product_id')
            logger.info(f'{onboard_object.group_id}{space:>{column_width - len(onboard_object.group_id)}}valid group_id')
            logger.info(f'{onboard_object.contract_id}{space:>{column_width - len(onboard_object.contract_id)}}valid contract_id')
        else:
            logger.error(f'{onboard_object.product_id}{space:>{column_width - len(onboard_object.product_id)}}invalid product_id')
            logger.warning(f'Available valid product_id for contract {onboard_object.contract_id}')
            count += 1
            products_list = sorted(product_detail['products'])
            for p in products_list:
                logger.warning(p)

        # network must be either STANDARD_TLS or ENHANCED_TLS
        if onboard_object.secure_network not in ['STANDARD_TLS', 'ENHANCED_TLS']:
            logger.error(f'{onboard_object.secure_network}{space:>{column_width - len(onboard_object.secure_network)}}invalid secure_network')
            count += 1

        # ensure hostname doesn't contain special characters and is of valid length
        reg = re.compile(r'[^\.\-a-zA-Z0-9]')
        for hostname in onboard_object.public_hostnames:
            if re.search(reg, hostname):
                logger.error(f'{hostname} contains invalid character. Only alphanumeric (a-z, A-Z, 0-9) and hyphen (-) characters are supported.')
                count += 1
            if len(hostname) > 60 and len(hostname) < 4:
                logger.error(f'{hostname} is invalid length. Hostname length must be between 4-60 characters')
                count += 1
            if (hostname[0] == '-') or (hostname[-1] == '-'):
                logger.error(f'{hostname} cannot begin or end with a hyphen.')
                count += 1

        # must be one of three valid modes
        edgeHostnameList = onboard_object.edge_hostname_list
        valid_modes = ['use_existing_edgehostname', 'secure_by_default']
        logger.info(f'{onboard_object.edge_hostname_mode}{space:>{column_width - len(onboard_object.edge_hostname_mode)}}edge hostname mode')
        if onboard_object.edge_hostname_mode == 'use_existing_edgehostname':
            ehn_id = 0
            # check to see if specified edge hostname exists
            for edgeHostname in edgeHostnameList:
                ehn_id = self.validateEdgeHostnameExists(wrapper_object, str(edgeHostname))
                public_hostname_str = ', '.join(onboard_object.public_hostnames)
                if ehn_id != 0:
                    logger.info(f'{edgeHostname} valid edge hostname (ehn_{ehn_id})')
                # logger.info(f'{public_hostname_str:<30}{space:>20}valid public hostname')
                    # onboard_object.edge_hostname_id = ehn_id
                else:
                    logger.error(f'{edgeHostname} invalid edge hostname')
                    count += 1
        elif onboard_object.edge_hostname_mode == 'secure_by_default':
            ehn_id = 0
            for i, edgeHostname in enumerate(edgeHostnameList):
                # check to see if specified edge hostname exists
                ehn_id = self.validateEdgeHostnameExists(wrapper_object, str(edgeHostname))
                public_hostname_str = ', '.join(onboard_object.public_hostnames)
                if ehn_id != 0:
                    logger.info(f'{edgeHostname} valid edge hostname (ehn_{ehn_id})')
                    # logger.info(f'{public_hostname_str:<30}{space:>20}valid public hostname')
                    # onboard_object.edge_hostname_id = ehn_id
                else:
                    if edgeHostname.endswith(('edgekey.net', 'edgesuite.net')):
                        logger.warning(f'{edgeHostname} does not exist, will be created upon property activation')
                    else:
                        logger.warning(f'{edgeHostname} does not end with edgekey.net or edgesuite.net, using {hostname}.{onboard_object.ehn_suffix}')
                        # no need to error out if ehn doesn't exist for SBD - ehn will get created with property activation
                        # count += 1

        # If supposed to something with WAF, can we find waf_config_id for the specifed name
        if cli_mode == 'batch-create':
            if not onboard_object.add_selected_host:
                if onboard_object.activate_waf_policy_staging and onboard_object.waf_config_name is None:
                    logger.error('If activating WAF to STAGING, waf-config must be provided')
                    count += 1
            else:
                if not onboard_object.activate_property_staging:
                    logger.error('to activate security config, you must activate delivery config on STAGING network')
                    count += 1

                if onboard_object.activate_waf_policy_staging:
                    config_detail = self.getWafConfigIdByName(wrapper_object, onboard_object.waf_config_name)
                    if config_detail['Found']:
                        onboard_object.onboard_waf_config_id = config_detail['details']['id']
                        onboard_object.onboard_waf_prev_version = config_detail['details']['latestVersion']
                        logger.debug(f'{onboard_object.onboard_waf_config_id} {onboard_object.onboard_waf_config_version}')
                        logger.info(f'{onboard_object.waf_config_name}{space:>{column_width - len(onboard_object.waf_config_name)}}valid waf_config_name')
                        logger.info(f'{onboard_object.onboard_waf_config_id}{space:>{column_width - len(str(onboard_object.onboard_waf_config_id))}}found existing onboard_waf_config_id')
                        logger.info(f'{onboard_object.onboard_waf_prev_version}{space:>{column_width - len(str(onboard_object.onboard_waf_prev_version))}}found latest onboard_waf_prev_version')
                    else:
                        count += 1
                        logger.error(f'{onboard_object.waf_config_name}{space:>{column_width - len(onboard_object.waf_config_name)}}invalid waf_config_name, not found')

                    if onboard_object.onboard_waf_config_id is not None:
                        logger.debug(f'{onboard_object.onboard_waf_config_id} {onboard_object.onboard_waf_prev_version}')
                        _, policies = wrapper_object.get_waf_policy(onboard_object)
                        _, target_ids = wrapper_object.list_match_targets(onboard_object.onboard_waf_config_id,
                                                                          onboard_object.onboard_waf_prev_version,
                                                                          policies)
                        if (onboard_object.update_match_target) and (onboard_object.waf_match_target_id in target_ids):
                            for k in policies:
                                if onboard_object.waf_match_target_id in policies[k]:
                                    logger.info(f'{policies[k][0]}{space:>{column_width - len(policies[k][0])}}found existing policy')
                                    logger.info(f'{onboard_object.waf_match_target_id}{space:>{column_width - len(str(onboard_object.waf_match_target_id))}}found waf_match_target_id')
                        else:
                            if not (onboard_object.update_match_target):
                                logger.debug('No match target given, updating selected hosts only')
                            else:
                                logger.error(f'{onboard_object.waf_match_target_id}{space:>{column_width - len(str(onboard_object.waf_match_target_id))}}invalid waf_match_target_id')
                                count += 1
                            # we will not auto correct waf_match_target_id
                            # onboard_object.waf_match_target_id = correct_target_id
                            # logger.info(f'{onboard_object.waf_match_target_id:<30}{space:>20}auto correct waf_match_target_id')
        else:
            pass

        # valid notify_emails is required
        emails = onboard_object.notification_emails

        # check if emails are empty and activation is true - can be [""] or []
        if (onboard_object.activate_property_staging or onboard_object.activate_property_production):
            if len(emails) == 0:
                logger.error('At least one valid notification email is required for activations')
                count += 1
            if len(emails) == 1:
                if emails[0] == '':
                    logger.error('At least one valid notification email is required for activations')
                    count += 1
            # check that emails are valid
            if len(emails) > 0:
                for email in emails:
                    if not is_email(email):
                        logger.error(f'{email}{space:>{column_width - len(email)}}invalid email address')
                        count += 1

        # maximum active security config per network is 10
        '''
        # apply to akamai internal account only
        if onboard_object.activate_waf_policy_staging and valid_waf:
            stg_active_count, prd_active_count = self.get_active_sec_config(wrapper_object)
            msg = 'Deactivate another one, or contact support to raise limits.'
            if stg_active_count >= 10:
                logger.error(f'You reached your maximum allowed number of security configurations on STAGING. {msg}')
                count += 1

            if onboard_object.activate_waf_policy_staging and prd_active_count >= 10:
                logger.error(f'You reached your maximum allowed number of security configurations on PRODUCTION. {msg}')
                count += 1
        '''

        if count == 0:
            self.valid is True
            print()
            logger.warning('Onboarding Delivery Config')
        else:
            sys.exit(logger.error('Please review all errors'))

        return self.valid

    def validateSetupSteps(self, onboard_object, wrapper_object, cli_mode='create') -> bool:
        """
        Function to validate the input values of setup.json
        """
        count = 0
        valid_waf = True
        print()
        logger.warning('Validating setup file information. Please wait, may take a few moments')

        # check if property name exists
        if wrapper_object.property_exists(onboard_object.property_name):
            logger.error(f'{onboard_object.property_name}{space:>{column_width - len(onboard_object.property_name)}}invalid property name; already in use')
            count += 1
        else:
            logger.info(f'{onboard_object.property_name}{space:>{column_width - len(onboard_object.property_name)}}valid property name')

        # use file or folder but not both
        if onboard_object.use_file and onboard_object.use_folder:
            logger.error('Both use_file and use_folder cannot be set to true')
            count += 1

        if not onboard_object.use_file and not onboard_object.use_folder:
            logger.error('Either use_file or use_folder must be set to true')
            count += 1

        # if create_new_cpcode, must specify a name
        if onboard_object.create_new_cpcode:
            if onboard_object.new_cpcode_name == '':
                logger.error('If create_new_cpcode is true, new_cpcode_name must be specified')
                count += 1

        # if use_file, template file and variable file must exist
        if onboard_object.use_file:
            if onboard_object.source_template_file == '':
                logger.error('If use_file is true, source_template_file must be specified')
                count += 1

            if onboard_object.source_values_file == '':
                logger.error('If use_file is true, source_values_file must be specified')
                count += 1

        # if use_folder, folder path and env_name must be specified
        if onboard_object.use_folder:
            if onboard_object.folder_path == '':
                logger.error('If use_folder is true, folder_path must be specified')
                count += 1

            if onboard_object.env_name == '':
                logger.error('If use_folder is true, env_name must be specified')
                count += 1

        # if activating pm to prod, must active to staging first
        if onboard_object.activate_property_production:
            if onboard_object.activate_property_staging is not True:
                logger.error('Must activate property to STAGING before activating to PRODUCTION')
                count += 1

        # must activate waf config to staging before activating waf to prodution
        if onboard_object.activate_waf_policy_production:
            if not onboard_object.activate_waf_policy_staging:
                logger.error('Must activate WAF policy to STAGING before activating to PRODUCTION.')
                count += 1

        # validate product id available per contract
        product_detail = self.validateProductId(wrapper_object,
                                                onboard_object.contract_id,
                                                onboard_object.product_id)
        if product_detail['Found']:
            logger.info(f'{onboard_object.product_id}{space:>{column_width - len(onboard_object.product_id)}}valid product_id')
            logger.info(f'{onboard_object.group_id}{space:>{column_width - len(onboard_object.group_id)}}valid group_id')
            logger.info(f'{onboard_object.contract_id}{space:>{column_width - len(onboard_object.contract_id)}}valid contract_id')
        else:
            logger.error(f'{onboard_object.product_id}{space:>{column_width - len(onboard_object.product_id)}}invalid product_id')
            logger.error(f'Available valid product_id for contract {onboard_object.contract_id}')
            count += 1
            products_list = sorted(product_detail['products'])
            for p in products_list:
                logger.error(p)

        # network must be either STANDARD_TLS or ENHANCED_TLS
        if onboard_object.secure_network not in ['STANDARD_TLS', 'ENHANCED_TLS']:
            logger.error(f'{onboard_object.secure_network}{space:>{column_width - len(onboard_object.secure_network)}}invalid secure_network')
            count += 1

        # ensure hostname doesn't contain special characters and is of valid length
        count = self.validate_hostnames(onboard_object.public_hostnames)

        # must be one of three valid modes
        valid_modes = ['use_existing_edgehostname', 'new_standard_tls_edgehostname', 'new_enhanced_tls_edgehostname', 'secure_by_default']
        logger.info(f'{onboard_object.edge_hostname_mode}{space:>{column_width - len(onboard_object.edge_hostname_mode)}}edge hostname mode')
        if onboard_object.edge_hostname_mode not in valid_modes:
            logger.error(f'{onboard_object.edge_hostname_mode}{space:>{column_width - len(onboard_object.edge_hostname_mode)}}invalid edge_hostname_mode')
            count += 1
            logger.info('valid options: use_existing_edgehostname, new_standard_tls_edgehostname, new_enhanced_tls_edgehostname')
        elif onboard_object.edge_hostname_mode == 'use_existing_edgehostname':
            ehn_id = 0
            if onboard_object.edge_hostname == '':
                logger.error(f'{onboard_object.edge_hostname}{space:>{column_width - len(onboard_object.edge_hostname)}}missing edge hostname')
                count += 1
            else:
                try:
                    # check to see if specified edge hostname exists
                    ehn_id = self.validateEdgeHostnameExists(wrapper_object, str(onboard_object.edge_hostname))
                    public_hostname_str = ', '.join(onboard_object.public_hostnames)
                    logger.info(f'ehn_{ehn_id}{space:>{column_width - len(str(ehn_id))-4}}valid edge_hostname_id')
                    logger.info(f'{onboard_object.edge_hostname}{space:>{column_width - len(onboard_object.edge_hostname)}}valid edge hostname')
                    if column_width - len(public_hostname_str) <= 0:
                        logger.info(f'{public_hostname_str} valid public hostname')
                    else:
                        logger.info(f'{public_hostname_str}{space:>{column_width - len(public_hostname_str)}}valid public hostname')
                    onboard_object.edge_hostname_id = ehn_id
                except:
                    logger.error(f'{onboard_object.edge_hostname}{space:>{column_width - len(onboard_object.edge_hostname)}}invalid edge hostname')
                    count += 1
        elif onboard_object.edge_hostname_mode == 'new_standard_tls_edgehostname':
            if onboard_object.secure_network != 'STANDARD_TLS':
                logger.error('For new_standard_tls_edgehostname, secure_network must be STANDARD_TLS')
                count += 1
        elif onboard_object.edge_hostname_mode == 'new_enhanced_tls_edgehostname':
            if onboard_object.secure_network != 'ENHANCED_TLS':
                logger.error('For new_enhanced_tls_edgehostname, secure_network must be ENHANCED_TLS')
                count += 1

            if onboard_object.use_existing_enrollment_id is True:
                if onboard_object.create_new_ssl_cert is True:
                    logger.error('Both use_existing_enrollment_id and create_new_ssl_cert cannot be set to true')
                    count += 1
                if onboard_object.existing_enrollment_id == 0:
                    logger.error(f"{'existing_enrollment_id'}{space:>{column_width - len(str(onboard_object.existing_enrollment_id))}}missing")
                    count += 1
            else:
                logger.error('If new_enhanced_tls_edgehostname, use_existing_enrollment_id must be true')
                count += 1

            if onboard_object.create_new_ssl_cert is True:
                logger.error('Unable to create_new_ssl_cert enrollment, please use existing_enrollment_id instead')
                count += 1
        elif onboard_object.edge_hostname_mode == 'secure_by_default':
            ehn_id = 0
            if onboard_object.secure_by_default_use_existing_ehn == '' and (not onboard_object.secure_by_default_new_ehn):
                logger.error(f'{onboard_object.edge_hostname}{space:>{column_width - len(onboard_object.edge_hostname)}}missing edge hostname')
                count += 1
            if (onboard_object.secure_by_default_use_existing_ehn != '') and (onboard_object.secure_by_default_new_ehn):
                logger.error('If create_new_edge_hostnames is true, use_existing_edge_hostnames must be empty')
                count += 1
            if (not onboard_object.secure_by_default_new_ehn) and (onboard_object.secure_by_default_use_existing_ehn != ''):
                try:
                    # check to see if specified edge hostname exists
                    ehn_id = self.validateEdgeHostnameExists(wrapper_object, str(onboard_object.secure_by_default_use_existing_ehn))
                    public_hostname_str = ', '.join(onboard_object.public_hostnames)
                    logger.info(f'ehn_{ehn_id}{space:>{column_width - len(str(ehn_id))+4}}valid edge_hostname_id')
                    logger.info(f'{onboard_object.secure_by_default_use_existing_ehn}{space:>{column_width - len(onboard_object.secure_by_default_use_existing_ehn)}}valid edge hostname')
                    logger.info(f'{public_hostname_str}{space:>{column_width - len(public_hostname_str)}}valid public hostname')
                    onboard_object.edge_hostname_id = ehn_id
                except:
                    logger.error(f'{onboard_object.secure_by_default_use_existing_ehn}{space:>{column_width - len(onboard_object.secure_by_default_use_existing_ehn)}}invalid edge hostname')
                    count += 1

        # validate source and variable file is use_file mode (create only)
        if onboard_object.use_file:
            if not self.validateFile('source_template_file', onboard_object.source_template_file):
                logger.error('unable to locate source_template_file')
                count += 1
            else:
                if onboard_object.source_values_file == '':
                    logger.error('missing source_values_file')
                    count += 1

            if not self.validateFile('source_values_file', onboard_object.source_values_file):
                logger.error('unable to locate source_values_file')
                count += 1

        # If supposed to something with WAF, can we find waf_config_id for the specifed name
        if cli_mode == 'create':
            if not onboard_object.add_selected_host:
                if onboard_object.update_match_target:
                    logger.error('If update_match_target, add_selected_host must be true')
                    count += 1
                if onboard_object.activate_waf_policy_staging:
                    logger.error('If activating WAF to STAGING, add_selected_host must be true')
                    count += 1
            else:
                if not onboard_object.activate_property_staging:
                    logger.error('If adding WAF selected hosts, property must be activated to STAGING')
                    count += 1
                '''
                # not require to activate WAF
                if not onboard_object.activate_waf_policy_staging:
                    logger.error('If adding WAF selected hosts, property must be activated to STAGING')
                    count += 1

                # if onboard_object.update_match_target and onboard_object.activate_waf_policy_staging:
                '''
                config_detail = self.getWafConfigIdByName(wrapper_object, onboard_object.waf_config_name)
                if config_detail['Found']:
                    onboard_object.onboard_waf_config_id = config_detail['details']['id']
                    onboard_object.onboard_waf_prev_version = config_detail['details']['latestVersion']
                    logger.debug(f'{onboard_object.onboard_waf_config_id} {onboard_object.onboard_waf_config_version}')
                    logger.info(f'{onboard_object.waf_config_name}{space:>{column_width - len(onboard_object.waf_config_name)}}valid waf_config_name')
                    logger.info(f'{onboard_object.onboard_waf_config_id}{space:>{column_width - len(str(onboard_object.onboard_waf_config_id))}}found existing onboard_waf_config_id')
                    logger.info(f'{onboard_object.onboard_waf_prev_version}{space:>{column_width - len(str(onboard_object.onboard_waf_prev_version))}}found latest onboard_waf_prev_version')
                else:
                    count += 1
                    logger.error(f'{onboard_object.waf_config_name}{space:>{column_width - len(onboard_object.waf_config_name)}}invalid waf_config_name, not found')

                if onboard_object.onboard_waf_config_id is not None:
                    logger.debug(f'{onboard_object.onboard_waf_config_id} {onboard_object.onboard_waf_prev_version}')
                    _, policies = wrapper_object.get_waf_policy(onboard_object)
                    _, target_ids = wrapper_object.list_match_targets(onboard_object.onboard_waf_config_id,
                                                                        onboard_object.onboard_waf_prev_version,
                                                                        policies)
                    if onboard_object.waf_match_target_id in target_ids:
                        for k in policies:
                            if onboard_object.waf_match_target_id in policies[k]:
                                logger.info(f'{policies[k][0]}{space:>{column_width - len(policies[k][0])}}found existing policy')
                                logger.info(f'{onboard_object.waf_match_target_id}{space:>{column_width - len(str(onboard_object.onboard_waf_config_id))-2}}found existing onboard_waf_config_id')
                    else:
                        logger.error(f'{onboard_object.waf_match_target_id}{space:>{column_width - len(str(onboard_object.onboard_waf_config_id))}}invalid onboard_waf_config_id')
                        count += 1
                        # we will not auto correct waf_match_target_id
                        # onboard_object.waf_match_target_id = correct_target_id
                        # logger.info(f'{onboard_object.waf_match_target_id:<30}{space:>20}auto correct waf_match_target_id')
        elif cli_mode in ['single-host', 'multi-hosts']:
            if onboard_object.edge_hostname and onboard_object.existing_enrollment_id > 0:
                logger.error('Only "use_existing_edge_hostname" or "create_from_existing_enrollment_id" can be used, not both')
                count += 1
            if onboard_object.use_existing_enrollment_id > 0:
                onboard_object.edge_hostname = onboard_object.public_hostnames[0]
                if cli_mode == 'multi-hosts':
                    # all public hostname use the same edge hostname prefix with property name
                    onboard_object.edge_hostname = onboard_object.property_name
                logger.debug(f'{cli_mode} {onboard_object.edge_hostname}')

            if onboard_object.create_new_security_config:
                config_detail = self.getWafConfigIdByName(wrapper_object, onboard_object.waf_config_name)
                if config_detail['Found']:
                    count += 1
                    onboard_object.onboard_waf_config_id = config_detail['details']['id']
                    onboard_object.onboard_waf_prev_version = config_detail['details']['latestVersion']
                    logger.error(f'{onboard_object.waf_config_name}{space:>{column_width - len(onboard_object.waf_config_name)}}duplicate waf_config_name already exists')
                    logger.info(f'{onboard_object.onboard_waf_config_id}{space:>{column_width - len(str(onboard_object.onboard_waf_config_id))}}found existing onboard_waf_config_id')
                    logger.info(f'{onboard_object.onboard_waf_prev_version}{space:>{column_width - len(str(onboard_object.onboard_waf_prev_version))}}found latest onboard_waf_prev_version')
                    valid_waf = False
                else:
                    # valid means this waf name doesn't exists
                    logger.info(f'{onboard_object.waf_config_name}{space:>{column_width - len(onboard_object.waf_config_name)}}new waf_config_name')

        else:
            pass

        # valid notify_emails is required
        emails = onboard_object.notification_emails

        # check if emails are empty and activation is true - can be [""] or []
        if (onboard_object.activate_property_staging or onboard_object.activate_property_production):
            if len(emails) == 0:
                logger.error('At least one valid notification email is required for activations')
                count += 1
            if len(emails) == 1:
                if emails[0] == '':
                    logger.error('At least one valid notification email is required for activations')
                    count += 1
            # check that emails are valid
            if len(emails) > 0:
                for email in emails:
                    if not is_email(email):
                        logger.error(f'{email}{space:>{column_width - len(email)}}invalid email address')
                        count += 1

        # maximum active security config per network is 10
        '''
        # apply to akamai internal account only
        if onboard_object.activate_waf_policy_staging and valid_waf:
            stg_active_count, prd_active_count = self.get_active_sec_config(wrapper_object)
            msg = 'Deactivate another one, or contact support to raise limits.'
            if stg_active_count >= 10:
                logger.error(f'You reached your maximum allowed number of security configurations on STAGING. {msg}')
                count += 1

            if onboard_object.activate_waf_policy_staging and prd_active_count >= 10:
                logger.error(f'You reached your maximum allowed number of security configurations on PRODUCTION. {msg}')
                count += 1
        '''

        if count == 0:
            self.valid is True
            print()
            logger.warning('Onboarding Delivery Config')
        else:
            sys.exit(logger.error(f'Total {count} errors, please review'))

        return self.valid

    def validateAppsecSteps(self, onboard_object, wrapper_object, cli_mode='appsec-update'):
        """
        Function to validate inputs for appsec-update
        """

        count = 0
        valid_waf = True
        print()
        logger.warning('Validating inputs. Please wait, may take a few moments')

        # check if csv is valid
        if not onboard_object.valid_csv:
            logger.error(f'{onboard_object.csv:<30}{space:>20}invalid CSV file; check above validation errors')
            count += 1

        if cli_mode == 'appsec-update':
            # check if config id exists
            msg = f'{onboard_object.config_id}{space:>{column_width-len(onboard_object.config_id)}}'
            appsec_configs = wrapper_object.getWafConfigurations()
            if appsec_configs.status_code == 200:
                appsec_configs = appsec_configs.json()
            else:
                sys.exit(logger.error('unable to get waf configurations....'))
            try:
                appsec_config_exists = list(filter(lambda x: int(x['id']) == int(onboard_object.config_id), appsec_configs['configurations']))
            except KeyError:
                sys.exit(logger.error('unable to get waf configurations....'))

            # return list of valid appsec ids if appsec id invalid
            if not appsec_config_exists:
                logger.error(f'{msg}invalid config id')
                valid_waf = False
                count += 1
                # listing valid waf configs and ids
                logger.warning('Showing all available configs...')
                logger.info(f'Config Name:{space:>38}Config Id:')
                for waf_config in appsec_configs['configurations']:
                    logger.info(f"{waf_config['name']}{space:>{column_width-len(waf_config['name'])}}{waf_config['id']}")
                sys.exit(logger.error('Exiting....'))
            else:
                onboard_object.waf_config_name = appsec_config_exists[0]['name']
                logger.info(f'{onboard_object.waf_config_name} {space:>{column_width-(len(onboard_object.waf_config_name))}}valid config name')
                logger.info(f'{onboard_object.config_id} {space:>{column_width-(len(onboard_object.config_id))}}valid config id')

            # check if config id base version exists
            if valid_waf:
                msg = f'{onboard_object.onboard_waf_prev_version}{space:>{column_width-len(onboard_object.onboard_waf_prev_version)}}'
                if onboard_object.onboard_waf_prev_version == 'latest':
                    onboard_object.onboard_waf_prev_version = appsec_config_exists[0]['latestVersion']
                    logger.info(f'{msg} using config id version {onboard_object.onboard_waf_prev_version}')
                else:
                    if int(onboard_object.onboard_waf_prev_version) > appsec_config_exists[0]['latestVersion']:
                        logger.error(f'{msg} invalid config version')
                        count += 1
                        valid_waf = False
                    else:
                        logger.info(f'{msg} valid config id version')

            # check if policy match targets are valid
            if valid_waf:
                # first get all policies
                policies = wrapper_object.get_waf_policy_update(onboard_object.config_id, onboard_object.onboard_waf_prev_version)
                if policies:
                    unique_match_target_list = list(set(list(map(lambda x: x['matchTargetId'], onboard_object.csv_dict))))
                    resp, waf_match_target_ids = wrapper_object.list_match_targets(onboard_object.config_id, onboard_object.onboard_waf_prev_version, policies)
                    for unique_match_target in unique_match_target_list:
                        msg = f'{unique_match_target}{space:>{column_width-len(unique_match_target)}}'
                        if int(unique_match_target) in waf_match_target_ids:
                            logger.debug(f'{msg} valid match target id')
                        else:
                            logger.error(f'{msg} invalid match target id')
                            count += 1
                    if resp.status_code != 200:
                        sys.exit(logger.error('unable to get waf match targets....'))
                else:
                    sys.exit(logger.error('unable to get waf policies....'))

                # validate that hostnames are either already selected or selectable
                available_hostnames = wrapper_object.getWAFSelectableHosts(onboard_object.config_id, onboard_object.onboard_waf_prev_version)
                selectable_hosts_list = list(set(list(map(lambda x: x['hostname'], available_hostnames['availableSet']))))
                try:
                    selected_host_list = list(set(list(map(lambda x: x['hostname'], available_hostnames['selectedSet']))))
                except KeyError:
                    selected_host_list = []

                if available_hostnames:
                    logger.debug(f'{onboard_object.hostname_list=}')
                    logger.debug(f'{selectable_hosts_list=}')
                    logger.debug(f'{selected_host_list=}')
                    for hostname in onboard_object.hostname_list:
                        if column_width - len(hostname) < 0:
                            msg = hostname
                        else:
                            msg = f'{hostname}{space:>{column_width-len(hostname)}}'
                        if hostname in selectable_hosts_list:
                            logger.info(f'{msg} valid selectable hostnames')
                        elif hostname in selected_host_list:
                            logger.warning(f'{msg} existing hostname')
                        else:
                            count += 1
                            logger.error(f'{msg} invalid selectable hostnames')
                            onboard_object.skip_selected_hosts.append(hostname)
                else:
                    sys.exit(logger.error('unable to get available hostnames'))

        if not self.validate_email(onboard_object.notification_emails):
            count += 1

        if count == 0:
            self.valid is True
            print()
            logger.warning('Updating Appsec Config')
        else:
            sys.exit(logger.error(f'Total {count} errors, please review'))

        return self.valid

    def validateCustomSteps(self, onboard: Onboard, papi: apiCallsWrapper) -> bool:
        """
        Function to validate the input values of env json and to populate onboard variables
        """
        count = 0
        print()
        logger.warning('Validating environment file information. Please wait, may take a few moments')

        if onboard.build_env not in onboard.env_details.keys():
            sys.exit(logger.error(f'{onboard.build_env} is not in environments.json environments'))
        else:
            logger.info(f'{onboard.build_env}{space:>{column_width - len(onboard.build_env)}}environment')

        if 'property_name' not in onboard.env_details[onboard.build_env].keys():
            logger.error(f'property_name{space:>{column_width - len("property_name")}}property name not found in environment file')
            count += 1
        else:
            onboard.property_name = onboard.env_details[onboard.build_env]['property_name']
            if not papi.property_exists(onboard.property_name):
                logger.error(f'{onboard.property_name}{space:>{column_width - len(onboard.property_name)}}property name on JSON file does not exist')
                count += 1
            else:
                logger.info(f'{onboard.property_name}{space:>{column_width - len(onboard.property_name)}}property name')

                onboard.property_details = papi.get_property_id(onboard.property_name)
                if not onboard.property_details:
                    logger.error(f'{space:>{column_width}}unable to get property details')
                    count += 1
                else:
                    # propertyId, groupId, contractId
                    onboard.property_id = onboard.property_details[0]['propertyId']
                    onboard.contract_id = onboard.property_details[0]['contractId']
                    onboard.group_id = onboard.property_details[0]['groupId']
                    logger.info(f'{onboard.property_id}{space:>{column_width - len(f"{onboard.property_id}")}}property id')
                    logger.info(f'{onboard.contract_id}{space:>{column_width - len(f"{onboard.contract_id}")}}contract id')
                    logger.info(f'{onboard.group_id}{space:>{column_width - len(f"{onboard.group_id}")}}group id')

                    # propertyVersion
                    onboard.property_latest_version = max(list(map(lambda x: x['propertyVersion'], onboard.property_details)))
                    try:
                        onboard.property_staging_version = list(filter(lambda x: x['stagingStatus'] == 'ACTIVE',
                                                                        onboard.property_details))[0]['propertyVersion']
                    except IndexError:
                        onboard.property_staging_version = None
                    try:
                        onboard.property_production_version = list(filter(lambda x: x['productionStatus'] == 'ACTIVE',
                                                                        onboard.property_details))[0]['propertyVersion']
                    except IndexError:
                        onboard.property_production_version = None

                    # version summary
                    empty = (column_width
                             - len(f'{onboard.property_latest_version}')  # noqa W503
                             - len(f'{onboard.property_staging_version}')  # noqa W503
                             - len(f'{onboard.property_production_version}')  # noqa W503
                             - 5)  # noqa W503
                    msg_versions = f'v{onboard.property_latest_version}:v{onboard.property_staging_version}:v{onboard.property_production_version}'
                    logger.info(f'{msg_versions}{space:>{empty}}latest:staging:production version')

                    if onboard.property_version.lower() == 'prod':
                        onboard.property_version_base = onboard.property_production_version
                        logger.info(f'v{onboard.property_version_base}{space:>{column_width - 1 - len(f"{onboard.property_version_base}")}}build from version')
                    elif onboard.property_version.lower() == 'staging':
                        onboard.property_version_base = onboard.property_staging_version
                        logger.info(f'v{onboard.property_version_base}{space:>{column_width - 1 - len(f"{onboard.property_version_base}")}}build from version')
                    elif onboard.property_version.lower() == 'latest':
                        onboard.property_version_base = onboard.property_latest_version
                        logger.info(f'v{onboard.property_version_base}{space:>{column_width - 1 - len(f"{onboard.property_version_base}")}}build from version')
                    else:
                        try:
                            onboard.property_version_base = int(onboard.property_version)
                            if onboard.property_version_base <= 0:
                                logger.error(f'v{onboard.property_version_base}{space:>{column_width - 2}}invalid property version must be >= 1')
                                count += 1
                            elif onboard.property_version_base > onboard.property_latest_version:
                                logger.error(f'v{onboard.property_version}{space:>{column_width - 1 - len(f"{onboard.property_version}")}}invalid property version')
                                count += 1
                            else:
                                logger.info(f'v{onboard.property_version_base}{space:>{column_width - 1 - len(f"{onboard.property_version_base}")}}build from version')
                        except (AttributeError, ValueError):
                            logger.error(f'{onboard.property_version}{space:>{column_width - len(f"{onboard.property_version}")}}invalid property version..must be integer')
                            count += 1
                            self.valid = False
                            return self.valid

                    # productId
                    onboard.product_id = papi.get_property_version_details(onboard.property_id, onboard.contract_id, onboard.group_id, onboard.property_version_base)
                    logger.info(f'{onboard.product_id}{space:>{column_width - len(f"{onboard.product_id}")}}product id')
                    if not onboard.product_id:
                        count += 1
                        logger.error(f'{space:>{column_width - 0}}unable to get product id')

            # rulename
            if 'property_rule_name' not in onboard.env_details[onboard.build_env].keys():
                logger.error(f'property_rule_name{space:>{column_width - len("property_rule_name")}}not found in environment file')
                count += 1
            else:
                onboard.property_rule_name = onboard.env_details[onboard.build_env]['property_rule_name']
                if onboard.property_rule_name.strip(' ') == '':
                    logger.error(f'{onboard.property_rule_name}{space:>{column_width - len(onboard.property_rule_name)}}rule name cannot be empty')
                    count += 1
                else:
                    logger.info(f'{onboard.property_rule_name}{space:>{column_width - len(onboard.property_rule_name)}}rule name to inject into')

            if 'property_https_paths_rule_name' not in onboard.env_details[onboard.build_env].keys():
                logger.error(f'property_https_paths_rule_name{space:>{column_width - len("property_https_paths_rule_name")}}not found in environment file')
                onboard.property_https_paths_rule_name = False
            else:
                onboard.property_https_paths_rule_name = onboard.env_details[onboard.build_env]['property_https_paths_rule_name']
                if onboard.property_rule_name.strip(' ') == '':
                    logger.error(f'{onboard.property_https_paths_rule_name}{space:>{column_width - len(onboard.property_https_paths_rule_name)}}rule is empty')
                    onboard.property_https_paths_rule_name = False
                else:
                    logger.info(f'{onboard.property_https_paths_rule_name}{space:>{column_width - len(onboard.property_https_paths_rule_name)}}rule name to add path matches into')

            # security
            onboard.waf_config_name = onboard.env_details[onboard.build_env]['waf_config_name']
            onboard.waf_policy_name = onboard.env_details[onboard.build_env]['waf_policy_name']
            onboard.waf_match_target_id = onboard.env_details[onboard.build_env]['waf_match_target_id']
            if onboard.waf_config_name == '':
                count += 1
                logger.error(f'{onboard.waf_config_name}{space:>{column_width - len(onboard.waf_config_name)}}invalid waf_config_name, not found')

            waf_config_detail = self.getWafConfigIdByName(papi, onboard.waf_config_name)
            if not waf_config_detail['Found']:
                count += 1
                logger.error(f'{onboard.waf_config_name}{space:>{column_width - len(onboard.waf_config_name)}}invalid waf_config_name, not found')

            if waf_config_detail['Found']:
                onboard.onboard_waf_config_id = waf_config_detail['details']['id']
                onboard.onboard_waf_prev_version = waf_config_detail['details']['latestVersion']
                if 'productionVersion' in waf_config_detail['details'].keys():
                    onboard.waf_prod_version = waf_config_detail['details']['productionVersion']
                else:
                    onboard.waf_prod_version = None
                if 'stagingVersion' in waf_config_detail['details'].keys():
                    onboard.waf_stage_version = waf_config_detail['details']['stagingVersion']
                else:
                    onboard.waf_stage_version = None

                logger.debug(f'{onboard.onboard_waf_config_id} {onboard.onboard_waf_prev_version} {onboard.waf_prod_version} {onboard.waf_stage_version}')
                logger.info(f'{onboard.waf_config_name}{space:>{column_width - len(onboard.waf_config_name)}}valid waf_config_name')
                logger.info(f'{onboard.onboard_waf_config_id}{space:>{column_width - len(str(onboard.onboard_waf_config_id))}}waf config id')

                empty = (column_width
                         - len(f'{onboard.onboard_waf_prev_version}')  # noqa W503
                         - len(f'{onboard.waf_stage_version}')  # noqa W503
                         - len(f'{onboard.waf_prod_version}')  # noqa W503
                         - 5)  # noqa W503
                msg_versions = f'v{onboard.onboard_waf_prev_version}:v{onboard.waf_stage_version}:v{onboard.waf_prod_version}'
                logger.info(f'{msg_versions}{space:>{empty}}latest:staging:production version')

                if onboard.onboard_waf_config_id is not None:
                    logger.debug(f'{onboard.onboard_waf_config_id} {onboard.waf_prod_version}')
                    _, policies = papi.get_waf_policy(onboard)
                    _, target_ids = papi.list_match_targets(onboard.onboard_waf_config_id,
                                                                        onboard.onboard_waf_prev_version,
                                                                        policies)

                    if onboard.waf_match_target_id in target_ids:
                        for k in policies:
                            if onboard.waf_match_target_id in policies[k]:
                                logger.info(f'{policies[k][0]}{space:>{column_width - len(policies[k][0])}}found existing policy')
                                logger.info(f'{onboard.waf_match_target_id}{space:>{column_width - len(str(onboard.waf_match_target_id))}}found waf_match_target_id')
                    else:
                        logger.error(f'{onboard.waf_match_target_id}{space:>{column_width - len(str(onboard.waf_match_target_id))}}invalid waf_match_target_id')
                        count += 1

        if len(onboard.paths) == 0:
            count += 1
            logger.error('rulename cannot be empty.  please check path column in input CSV file')

        if count == 0:
            self.valid = True
        else:
            self.valid = False
            sys.exit(logger.error(f'Total {count} errors, please review'))

        return self.valid

    def validateFile(self, source: str, file_location: str) -> bool:
        logger.debug(f'{file_location} {type(file_location)} {os.path.exists(file_location)}')
        logger.debug(os.path.abspath(file_location))
        return os.path.isfile(os.path.abspath(file_location))

    def validateProductId(self, wrapper_object, contract_id, product_id) -> dict:
        """
        Function to validate product ids for a contract
        """
        products = dict()
        products['Found'] = False
        products['products'] = []
        get_products_response = wrapper_object.getProductsByContract(contract_id)
        if get_products_response.status_code == 200:
            items = get_products_response.json()['products']['items']
            for each_item in items:
                if 'productId' in each_item:
                    if each_item['productId'] == product_id:
                        products['Found'] = True
                    products['products'].append(each_item['productId'])
        else:
            print(json.dumps(get_products_response.json(), indent=4))
        return products

    def validateEdgeHostnameExists(self, wrapper_object, edge_hostname) -> bool:
        """
        Function to validate edge hostname
        """
        ehn_id = 0
        edgehostname_response = wrapper_object.checkEdgeHostname(edge_hostname)
        record_name = edge_hostname
        if str(edge_hostname).endswith('edgekey.net'):
            record_name = str(edge_hostname).split('.edgekey.net')[0]
        elif str(edge_hostname).endswith('edgesuite.net'):
            record_name = str(edge_hostname).split('.edgesuite.net')[0]
        if edgehostname_response.status_code == 200:
            ehns = edgehostname_response.json()['edgeHostnames']
            for every_ehn in ehns:
                if every_ehn['recordName'] == record_name:
                    ehn_id = every_ehn['edgeHostnameId']
                    logger.debug(f'{ehn_id}{space:>{column_width - len(str(ehn_id))}}found edgeHostnameId')
                    return ehn_id
                else:
                    pass
        else:
            return 0
        return ehn_id

    def getWafConfigIdByName(self, wrapper_object, config_name) -> dict:
        """
        Function to get WAF config ID and version
        """
        config_detail = dict()
        config_detail['Found'] = False
        waf_configs_response = wrapper_object.getWafConfigurations()
        if waf_configs_response.ok:
            configurations = waf_configs_response.json()['configurations']
            for each_config in configurations:
                if 'name' in each_config:
                    if each_config['name'] == config_name:
                        config_detail['Found'] = True
                        config_detail['details'] = each_config
        return config_detail

    def doCliPipelineMerge(self, config, onboard_object, create_mode=True, merge_type='pm') -> bool:
        """
        Function to use Akamai property-manager CLI and merge template
        """
        # For PM merge, it will use temp_pm folder
        # For CPS merge, it will use temp_cps folder
        # Delete these folders if they exist to start

        if os.path.exists('temp_pm'):
            shutil.rmtree('temp_pm')
        if os.path.exists('temp_cps'):
            shutil.rmtree('temp_cps')
        try:
            os.remove('devops.log')
        except:
            pass

        try:
            os.remove('devops-logs.log')
        except:
            pass

        try:
            if create_mode:
                # Build projectInfo contents
                projectInfo = dict(environments=['test'], name=f'temp_{merge_type}')

                # Create pipeline specific folders are files
                if not os.path.exists(os.path.join(f'temp_{merge_type}', 'dist')):
                    os.makedirs(os.path.join(f'temp_{merge_type}', 'dist'))
                if not os.path.exists(os.path.join(f'temp_{merge_type}', 'environments', 'test')):
                    os.makedirs(os.path.join(f'temp_{merge_type}', 'environments', 'test'))
                if not os.path.exists(os.path.join(f'temp_{merge_type}', 'templates')):
                    os.makedirs(os.path.join(f'temp_{merge_type}', 'templates'))

                with open(os.path.join(f'temp_{merge_type}', 'projectInfo.json'), 'w') as projectFile:
                    projectFile.write(json.dumps(projectInfo, indent=4))

                if merge_type == 'pm':
                    templateFile = onboard_object.source_template_file
                    valuesFile = onboard_object.source_values_file
                else:
                    templateFile = onboard_object.ssl_cert_template_file
                    valuesFile = onboard_object.ssl_cert_template_values

                # Create main.json with contents of templateContent
                with open(templateFile) as templateHandler:
                    templateData = json.load(templateHandler)
                with open(os.path.join(f'temp_{merge_type}',
                                        'templates', 'main.json'), 'w') as mainContentHandler:
                    mainContentHandler.write(json.dumps(templateData, indent=4))

                # Create values file for test env from variables
                with open(valuesFile) as valuesHandler, \
                     open(os.path.join(f'temp_{merge_type}',
                                        'environments', 'test', 'variables.json'),
                                        'w') as testValuesHandler:
                    value_json = valuesHandler.read()
                    testValuesHandler.write(value_json)

                # Prepare the variable definitions file contents
                varDefinitions = {}
                varDefinitions['definitions'] = {}
                for eachKey in json.loads(value_json).keys():
                    varDefinitions['definitions'][eachKey] = {}
                    varDefinitions['definitions'][eachKey]['default'] = ''
                    varDefinitions['definitions'][eachKey]['type'] = 'userVariableValue'

                with open(os.path.join(f'temp_{merge_type}',
                                        'environments', 'variableDefinitions.json'),
                                        'w') as definitionHandler:
                    definitionHandler.write(json.dumps(varDefinitions, indent=4))

                # Create envInfo.json else it will error out
                testEnvInfo = dict(name='test')
                with open(os.path.join(f'temp_{merge_type}',
                                       'environments', 'test', 'envInfo.json'),
                                       'w') as testValuesHandler:
                    testValuesHandler.write(json.dumps(testEnvInfo, indent=4))

                # Run pipeline merge
                if merge_type == 'pm':
                    command = ['akamai', 'pipeline', 'merge',
                               '-n', '-p', 'temp_pm', 'test', '--edgerc',
                               config.edgerc, '--section', config.section]
                    command_str = ' '.join(command)
                    logger.debug(f'Success command: {command_str}')
                    child_process = subprocess.Popen(command,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT)
                    stdout, stderr = child_process.communicate()
                    rtn_code = child_process.returncode
                else:
                    command = ['akamai', 'pipeline', 'merge',
                               '-n', '-p', 'temp_cps', 'test', '--edgerc',
                               config.edgerc, '--section', config.section]
                    command_str = ' '.join(command)
                    logger.debug(f'Success command: {command_str}')
                    child_process = subprocess.Popen(command,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT)
                    stdout, stderr = child_process.communicate()
                    rtn_code = child_process.returncode
            else:
                # Copy the folder and run pipeline merge
                copy_tree(onboard_object.folder_path, 'temp_pm')

                # Read the projectInfo file to update the name of it
                with open(os.path.join('temp_pm', 'projectInfo.json')) as f:
                    content = json.loads(f.read())
                    content['name'] = 'temp_pm'

                # Write the projectInfo file with updated name
                with open(os.path.join('temp_pm', 'projectInfo.json'), 'w') as f:
                    f.write(json.dumps(content, indent=4))

                command = ['akamai', 'pipeline', 'merge', '-n', '-p', 'temp_pm',
                           onboard_object.env_name, '--edgerc', config.edgerc,
                           '--section', config.section]
                command_str = ' '.join(command)
                logger.debug(f'Success command: {command_str}')
                child_process = subprocess.Popen(command,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
                stdout, stderr = child_process.communicate()
                rtn_code = child_process.returncode

            # If pipeline merge command was not successful, return false
            if rtn_code != 0:
                logger.error('Merging the template file failed')
                logger.info(stdout)
                logger.error(stderr)
                return False

            # Process call worked, return true
            return True

        except Exception as e:
            logger.error(e)
            logger.error('Exception occurred while trying to merge. '
                  'Check devops-logs.log and/or temp_* folder '
                  'to see if files were copied or merged correctly')
            return False

    def get_active_sec_config(self, wrapper_object):
        config = wrapper_object.getWafConfigurations()
        config_ids, responses, stg, prd = [], [], [], []
        try:
            if len(config.json()['configurations']) > 0:
                config_list = config.json()['configurations']
                config_ids = [i['id'] for i in config_list if i['id']]
        except:
            pass

        for config_id in config_ids:
            responses.append(wrapper_object.get_waf_sec_detail(config_id).json())
        stg = [r['stagingActiveVersion'] for r in responses if 'stagingActiveVersion' in r.keys()]
        prd = [r['productionActiveVersion'] for r in responses if 'productionActiveVersion' in r.keys()]
        logger.debug(f'{len(stg)}-{len(prd)}')
        return len(stg), len(prd)

    def csv_2_origin_rules(self, csv_file_loc: str) -> dict:
        cli_path = f'{root}/templates/akamai_product_templates/behaviors'
        logger.info(f'Validating customer hostname input: {csv_file_loc}')

        if not self.validateFile('csv file', csv_file_loc):
            sys.exit(logger.error(f'{csv_file_loc}...........missing'))

        csv_file_loc = os.path.abspath(csv_file_loc)
        with open(csv_file_loc, encoding='utf-8-sig') as f:
            rows = sum(1 for row in f)
            if rows > 600:
                logger.warning(f'{rows} hostnames/origins defined. Consider splitting hostnames into multiple properties')

        public_hostnames, origin_hostnames = [], []
        with open(csv_file_loc, encoding='utf-8-sig') as f:
            parent_rule = {}
            parent_rule['name'] = 'Origin Rules'
            parent_rule['behaviors'] = []
            parent_rule['criteria'] = []
            parent_rule['children'] = []
            parent_rule['comments'] = 'Route request to appropriate origin'

            rows_reader = csv.reader(f, delimiter=',')
            for row in rows_reader:
                public_hostnames.append(row[0])
                origin_hostnames.append(row[1])
                origin_behavior_file = os.path.abspath(f'{cli_path}/origin.json')
                with open(origin_behavior_file) as t:
                    content = t.read()
                content = content.replace('$env.hostname', row[0])
                content = content.replace('$env.origin_name', row[1])
                parent_rule['children'].append(json.loads(content))
        logger.debug(json.dumps(parent_rule, indent=4))
        return parent_rule, public_hostnames, origin_hostnames

    def validate_prerequisite_cli(self) -> None:
        cli_installed = self.installedCommandCheck('akamai')
        pipeline_installed = self.executeCommand(['akamai', 'pipeline'])

        if not (pipeline_installed and (cli_installed or pipeline_installed)):
            sys.exit()

    def onboard_override_default(self, onboard, setup, cli_mode: str) -> None:
        if cli_mode == 'single-host':
            onboard.new_cpcode_name = setup.new_cpcode_name
            onboard.group_id = setup.group_id
            onboard.secure_network = 'STANDARD_TLS' if setup.edge_hostname.endswith('edgesuite.net') else onboard.secure_network
            template_path = f'{root}/templates/akamai_product_templates'
            onboard.source_values_file = f'{template_path}/single_variable.json'
        elif cli_mode == 'multi-hosts':
            onboard.group_id = setup.group_id
            template_path = f'{root}/templates/akamai_product_templates/multi-hosts'
            onboard.source_values_file = f'{template_path}/variables.json'

        onboard.source_template_file = f'{template_path}/{setup.product_id}.json'
        logger.info(f'Rule Template Location: {onboard.source_template_file}')
        onboard.create_new_security_config = setup.create_new_security_config
        if len(setup.waf_config_name) > 0:
            onboard.waf_config_name = setup.waf_config_name
        if setup.existing_enrollment_id > 0:
            onboard.use_existing_enrollment_id = True
            onboard.edge_hostname_mode = 'new_enhanced_tls_edgehostname'
            onboard.existing_enrollment_id = setup.existing_enrollment_id
        if not (setup.version_notes == ''):
            onboard.version_notes = setup.version_notes
        if not setup.activate_production:
            onboard.activate_property_production = False
            onboard.activate_waf_policy_production = False
        if onboard.secure_by_default:
            onboard.edge_hostname_mode = 'secure_by_default'

    def csv_validator(self, onboard_object, csv_file_loc: str):
        csv_dict = []
        schema = {
            'hostname': {
                'type': 'string',
                'required': True,
                'empty': False
            },
            'origin': {
                'type': 'string',
                'required': True,
                'empty': False
            },
            'propertyName': {
                'type': 'string'
            },
            'forwardHostHeader': {
                'type': 'string',
                'nullable': True,
                'allowed': ['REQUEST_HOST_HEADER', 'ORIGIN_HOSTNAME']
            },
            'edgeHostname': {
                'type': 'string',
                'regex': (r'(.*\.edgekey\.net$|.*\.edgesuite\.net$)')}
        }

        v = Validator(schema)
        logger.warning(f'Reading customer property name input: {csv_file_loc}')

        with open(csv_file_loc, encoding='utf-8-sig', newline='') as f:
            for i, row in enumerate(csv.DictReader(f), 1):
                csv_dict.append(row)
                valid = v.validate(row)
                validation_errors = v.errors
                if validation_errors:
                    onboard_object.valid_csv = False
                    logger.warning(f'CSV Validation Error in row: {i}...')
                    for error in validation_errors:
                        logger.warning(f'{error} {validation_errors[error]}')

        onboard_object.csv_dict = csv_dict
        return onboard_object.valid_csv

    def csv_validator_appsec(self, onboard_object, csv_file_loc: str):
        csv_dict = []
        schema = {
            'hostname': {
                'type': 'string',
                'required': True,
                'empty': False
            },
            'matchTargetId': {
                'required': False,
                'empty': True
            }
        }

        v = Validator(schema)
        logger.warning(f'Reading csv input: {csv_file_loc}')

        with open(csv_file_loc, encoding='utf-8-sig', newline='') as f:
            for i, row in enumerate(csv.DictReader(f), 1):
                csv_dict.append(row)
                valid = v.validate(row)
                validation_errors = v.errors
                if validation_errors:
                    onboard_object.valid_csv = False
                    logger.warning(f'CSV Validation Error in row: {i}...')
                    for error in validation_errors:
                        logger.warning(f'{error} {validation_errors[error]}')

        onboard_object.csv_dict = csv_dict
        return onboard_object.valid_csv

    def csv_2_property_dict(self, onboard_object) -> dict:
        propertyList = []
        hostnameList = []
        edgeHostnameList = []
        ehn_suffix = onboard_object.ehn_suffix
        if onboard_object.secure_network == 'STANDARD_TLS':
            ehn_suffix = '.edgesuite.net'

        for i, row in enumerate(onboard_object.csv_dict):
            try:
                propertyName = row['propertyName']
                if (propertyName is None) or (propertyName == ''):
                    propertyName = row['hostname']
            except KeyError:
                propertyName = row['hostname']
            hostname = row['hostname']
            hostnameList.append(hostname)
            propertyList.append(propertyName)
            try:
                edgeHostname = row['edgeHostname']
                if (edgeHostname is None) or (edgeHostname == ''):
                    if onboard_object.edge_hostname_mode == 'secure_by_default':
                        edgeHostnameList.append(f'{hostname}{ehn_suffix}')
                        logger.debug(f'edgeHostname value is empty - using edge hostname {hostname}{ehn_suffix}')
                    else:
                        sys.exit(logger.error(f'No edgeHostname provided for {hostname} - row:{i+1}'))
                else:
                    edgeHostnameList.append(edgeHostname)
            except KeyError:
                if onboard_object.edge_hostname_mode == 'secure_by_default':
                    edgeHostnameList.append(f'{hostname}{ehn_suffix}')
                    logger.debug(f'edgeHostname column does not exist in csv, using edge hostname {hostname}{ehn_suffix}')
                else:
                    sys.exit(logger.error('edgeHostname column must exist in input csv unless using secure-by-default mode'))

        propertyList = list(set(propertyList))
        hostnameList = list(set(hostnameList))

        onboard_object.edge_hostname_list = edgeHostnameList
        onboard_object.property_list = propertyList
        onboard_object.public_hostnames = hostnameList

        return (propertyList, hostnameList)

    def csv_2_property_array(self, config, onboard_object, cpcodeList) -> dict:
        cli_path = f'{root}/templates/akamai_product_templates/behaviors'
        propertyJson = {}
        hostnameList = []
        templateFile = onboard_object.source_template_file

        if not self.validateFile('json file', templateFile):
            sys.exit(logger.error(f'{templateFile}...........missing'))

        with open(templateFile) as templateHandler:
            templateData = json.load(templateHandler)

        # update template to include origin and cpCode behaviors in default rule if they don't exist
        default_behaviors = templateData['rules']['behaviors']
        onboard_object.level_0_rules = templateData['rules']['children']
        default_behavior_names = list(set(list(map(lambda x: x['name'], default_behaviors))))
        if 'origin' not in default_behavior_names:
            logger.warning('No default origin behavior in provided template, adding.....')
            with open(f'{cli_path}/origin_csv.json') as t:
                content = json.load(t)
                originBehavior = content['behaviors'][0]
                originBehavior['options']['forwardHostHeader'] = 'REQUEST_HOST_HEADER'
            templateData['rules']['behaviors'].append(originBehavior)
        if 'cpCode' not in default_behavior_names:
            logger.warning('No default cpCode behavior in provided template, adding.....')
            with open(f'{cli_path}/cpCode.json') as c:
                cp_content = json.load(c)
            templateData['rules']['behaviors'].append(cp_content)

        for i, row in enumerate(onboard_object.csv_dict):

            # group by propertyName
            propertyName = row['hostname']
            edgeHostname = onboard_object.edge_hostname_list[i]

            try:
                propertyName = row['propertyName']
                if (propertyName is None) or (propertyName == ''):
                    propertyName = row['hostname']

                # check to see if property already exists in dict if it does, add hostname, origins, ehns, to hostname dict and move on to next row
                else:
                    if propertyName in propertyJson.keys():
                        propertyJson[propertyName]['hostnames'].append(row['hostname'])
                        propertyJson[propertyName]['origins'].append(row['origin'])
                        propertyJson[propertyName]['edgeHostnames'].append(edgeHostname)
                        try:
                            if row['forwardHostHeader'] is not None:
                                propertyJson[propertyName]['forwardHostHeader'].append(row['forwardHostHeader'])
                            else:
                                propertyJson[propertyName]['forwardHostHeader'].append('REQUEST_HOST_HEADER')
                        except KeyError:
                            propertyJson[propertyName]['forwardHostHeader'].append('REQUEST_HOST_HEADER')

                        hostnameList.append(row['hostname'])
                        continue

        # If property doesn't already exist, add new property json rule tree to dict
            except KeyError:
                propertyName = row['hostname']

            propertyJson[propertyName] = {}
            propertyJson[propertyName]['ruleTree'] = templateData
            propertyJson[propertyName]['hostnames'] = [row['hostname']]
            propertyJson[propertyName]['origins'] = [row['origin']]
            propertyJson[propertyName]['edgeHostnames'] = [edgeHostname]
            try:
                propertyJson[propertyName]['forwardHostHeader'] = [row['forwardHostHeader']]

                if row['forwardHostHeader'] is None:
                    propertyJson[propertyName]['forwardHostHeader'] = ['REQUEST_HOST_HEADER']
            except KeyError:
                propertyJson[propertyName]['forwardHostHeader'] = ['REQUEST_HOST_HEADER']

            hostnameList.append(row['hostname'])

        # create origin behaviors for multi-origin setup
        for propertyName in propertyJson:

            if len(propertyJson[propertyName]['origins']) > 1:
                with open(f'{cli_path}/origin_csv.json') as t:
                    content = t.read()
                with open(f'{cli_path}/cpCode.json') as c:
                    cp_content = c.read()

                parent_rule = {}
                parent_rule['name'] = 'Origin Rules'
                parent_rule['behaviors'] = []
                parent_rule['criteria'] = []
                parent_rule['children'] = []
                parent_rule['comments'] = 'Route request to appropriate origin'

                # check default rule FOSSL settings (verificationMode: CUSTOM or verificationMode: PLATFORM_SETTINGS)
                default_fossl_verification_settings = ''
                for defaultBehavior in propertyJson[propertyName]['ruleTree']['rules']['behaviors']:
                    if defaultBehavior['name'] == 'origin':
                        default_fossl_verification_settings = defaultBehavior['options']['verificationMode']

                for i in range(len(propertyJson[propertyName]['origins'])):
                    originJson = content.replace('$env.hostname', propertyJson[propertyName]['hostnames'][i])
                    originJson = originJson.replace('$env.origin_name', propertyJson[propertyName]['origins'][i])
                    originJson = originJson.replace('$env.forward_host_header', propertyJson[propertyName]['forwardHostHeader'][i])
                    originJson = json.loads(originJson)
                    cpcodeJson = json.loads(cp_content)
                    cpcodeJson['options']['value']['id'] = cpcodeList[propertyJson[propertyName]['hostnames'][i]]

                    # update new origin behaviors to match verification setting of default rule
                    if default_fossl_verification_settings == 'PLATFORM_SETTINGS':
                        originJson['behaviors'][0]['options']['verificationMode'] = 'PLATFORM_SETTINGS'
                        platform_setting_keys_to_remove = ['customValidCnValues', 'originCertsToHonor', 'standardCertificateAuthorities']
                        for key in platform_setting_keys_to_remove:
                            del originJson['behaviors'][0]['options'][key]

                    originJson['behaviors'].append(cpcodeJson)
                    parent_rule['children'].append(originJson)

                propertyJson[propertyName]['originRule'] = parent_rule

        return (propertyJson, hostnameList)

    def csv_2_path_array(self, filepath: str) -> list:
        paths = []
        try:
            with open(filepath, encoding='utf-8-sig', newline='') as f:
                for _, row in enumerate(csv.DictReader(f), 1):
                    path = row['path']
                    pattern = r'/([^/-]+)-'
                    match = re.search(pattern, path)
                    if match:
                        value_before_hyphen = match.group(1)
                        paths.append({'path_match': row['path'],
                                      'rulename': value_before_hyphen.upper()})
        except FileNotFoundError as err:
            logger.error(err)
        return paths

    def validate_group_id(self, onboard, groups) -> None:
        for group in groups:
            if group['contractIds'][0] == onboard.contract_id:
                onboard.group_id = group['groupId']
                exit
        if onboard.group_id is None:
            sys.exit(logger.error('Unknown Error: Cannot find top level group_id'))

    def log_cli_timing(self) -> None:
        print()
        end_time = time.perf_counter()
        elapse_time = str(strftime('%H:%M:%S', gmtime(end_time - self.start_time)))
        logger.info(f'TOTAL DURATION: {elapse_time}, End Akamai CLI onboard')

    def validate_hostnames(self, hostnames) -> int:
        # ensure hostname doesn't contain special characters and is of valid length
        reg = re.compile(r'[^\.\-a-zA-Z0-9]')
        error_count = 0
        for hostname in hostnames:
            if re.search(reg, hostname):
                logger.error(f'{hostname} contains invalid character. Only alphanumeric (a-z, A-Z, 0-9) and hyphen (-) characters are supported.')
                error_count += 1
            if len(hostname) > 60 and len(hostname) < 4:
                logger.error(f'{hostname} is invalid length. Hostname length must be between 4-60 characters')
                error_count += 1
            if (hostname[0] == '-') or (hostname[-1] == '-'):
                logger.error(f'{hostname} cannot begin or end with a hyphen.')
                error_count += 1
        return error_count

    def csv_2_appsec_array(self, onboard_object) -> dict:
        hostname_list = []
        appsec_json = {}

        for i, row in enumerate(onboard_object.csv_dict):
            policyName = row['matchTargetId']
            # Check if policyName already exists in dictionary and append hostname to list
            if policyName in appsec_json.keys():
                appsec_json[policyName]['hostnames'].append(row['hostname'])
                hostname_list.append(row['hostname'])

            # If policy doesn't already exist in dict, add policy to dictionary and add hostname to list
            else:
                appsec_json[policyName] = {}
                appsec_json[policyName]['hostnames'] = [row['hostname']]
                hostname_list.append(row['hostname'])

        onboard_object.hostname_list = hostname_list
        onboard_object.appsec_json = appsec_json

    def validate_waf_config_name(self, wrapper_object, config_name: str | None = None) -> int:
        if config_name:
            config_detail = self.getWafConfigIdByName(wrapper_object, config_name)
            if config_detail['Found']:
                onboard_waf_config_id = config_detail['details']['id']
                onboard_waf_prev_version = config_detail['details']['latestVersion']
                logger.info(f'{config_name}{space:>{column_width - len(config_name)}}valid waf_config_name')
                logger.info(f'{onboard_waf_config_id}{space:>{column_width - len(str(onboard_waf_config_id))}}found existing onboard_waf_config_id')
                logger.info(f'{onboard_waf_prev_version}{space:>{column_width - len(str(onboard_waf_prev_version))}}found latest onboard_waf_prev_version')
            else:
                sys.exit(logger.error(f'{config_name}{space:>{column_width - len(config_name)}}invalid waf_config_name, not found'))
            return onboard_waf_config_id, onboard_waf_prev_version, pd.DataFrame()
        else:
            onboard_waf_config_id = 0
            onboard_waf_prev_version = 0
            response = wrapper_object.getWafConfigurations()
            df = pd.DataFrame(response.json()['configurations'])
            df.fillna('', inplace=True)
            return onboard_waf_config_id, onboard_waf_prev_version, df

    def list_waf_policy(self, wrapper_object, config_id, version, policy_name: str | None = None) -> str:
        _, policies = wrapper_object.get_waf_policy_from_config(config_id, version)
        if not policies:
            sys.exit(logger.error('This configuration does not have any policy'))
        else:
            df = pd.DataFrame.from_dict(policies, orient='index')
            df.index.name = 'Policy ID'
            df.columns = ['Policy Name']
            df.sort_values(by='Policy Name', inplace=True)
            if not policy_name:
                policy_str_id = ''
                # logger.warning('Security Policy')
                # print(tabulate(df, headers='keys', tablefmt='psql', showindex=True))
            else:
                try:
                    policy_str_id = list(filter(lambda x: policies[x] == [policy_name], policies))[0]
                    logger.info(f'{policy_name}{space:>{column_width - len(policy_name)}}valid policy name')
                    logger.info(f'{policy_str_id}{space:>{column_width - len(policy_str_id)}}found policy id')
                except:
                    # show all policies instead
                    print(tabulate(df, headers='keys', tablefmt='psql', showindex=True))
                    logger.warning(f'policy name "{policy_name}" not found.  Name must be exact match.')
                    return None, policies
        return policy_str_id, policies

    def csv_2_appsec_create_by_hostname(self, csv_file_loc: str):
        schema = {'waf_config_name': {'type': 'string',
                                      'empty': False,
                                      'required': False},
                  'waf_policy_name': {'type': 'string',
                                      'empty': False,
                                      'required': False},
                  'hostname': {'type': 'string',
                               'empty': False,
                               'required': False},
                 }

        v = Validator(schema)
        logger.warning(f'Reading customer security configuration input: {csv_file_loc}')
        valid = True
        with open(csv_file_loc, encoding='utf-8-sig', newline='') as f:
            data = []
            for i, row in enumerate(csv.DictReader(f), 1):
                data.append(row)
                v.validate(row)
                if v.errors:
                    valid = False
                    for error in v.errors:
                        logger.error(f'CSV Validation Error in row: {i} {error}')
        return valid, data

    def csv_2_appsec_create_by_propertyname(self, csv_file_loc: str):
        schema = {'property_name': {'type': 'string',
                                    'empty': False,
                                    'required': True},
                  'waf_config_name': {'type': 'string',
                                      'empty': False,
                                      'required': True},
                  'waf_policy_name': {'type': 'string',
                                       'empty': False,
                                       'required': True},
                  'hostname': {'type': 'string',
                               'nullable': True,
                               'required': False}
                 }

        v = Validator(schema)
        logger.warning(f'Reading customer security configuration input: {csv_file_loc}')
        valid = True
        with open(csv_file_loc, encoding='utf-8-sig', newline='') as f:
            data = []
            for i, row in enumerate(csv.DictReader(f), 1):
                data.append(row)
                v.validate(row)
                if v.errors:
                    valid = False
                    for error in v.errors:
                        logger.error(f'CSV Validation Error in row: {i} {error}')
        return valid, data

    def populate_waf_data(self, by: str, input: dict) -> dict:
        waf = []

        for i in input['waf_config_name'].unique():
            config = {}
            waf_policy_name = sorted(list({input['waf_policy_name'][j] for j in input[input['waf_config_name'] == i].index}))
            config['waf_config_name'] = i
            for policy in waf_policy_name:
                new_df = input[(input['waf_config_name'] == i) & (input['waf_policy_name'] == policy)]
                if by == 'propertyname':
                    combined_hostnames = new_df['hostname'].values
                    hostnames = [item for sublist in combined_hostnames for item in sublist]
                    combined_waf_target_hostnames = new_df['waf_target_hostname'].values
                    waf_target_hostnames = [item for sublist in combined_waf_target_hostnames for item in sublist]
                if by == 'hostname':
                    hostnames = new_df['hostname'].unique().tolist()
                    waf_target_hostnames = []
                config[policy] = (hostnames, waf_target_hostnames)
            waf.append(config)
        return waf

    def stringToList(self, input):
        try:
            if isinstance(input, list):
                newList = input
            elif isinstance(input, str) and len(input) != 0:
                tempList = input.split(', ')
                newList = list(map(lambda x: x, tempList))
            else:
                newList = []
        except:
            newList = None
        return (newList)

    def validate_email(self, emails: list) -> bool:
        if len(emails) > 0:
            for email in emails:
                if not is_email(email):
                    logger.error(f'{email}{space:>{column_width - len(email)}}invalid email address')
                    return False
        return True

    def validate_appsec_pre_create(self, main_object, wrap_api, util_waf, selectable_df):
        """
        Function to validate inputs for appsec-create
        """
        count = 0
        by = main_object.template
        csv = main_object.csv
        activate = main_object.activate
        network = main_object.network
        contract_id = main_object.contract_id
        group_id = main_object.group_id

        if main_object.template == 'propertyname':
            valid_csv, data = self.csv_2_appsec_create_by_propertyname(csv)
        else:
            valid_csv, data = self.csv_2_appsec_create_by_hostname(csv)
        if valid_csv is False:
            logger.error('CSV input needs to be corrected first')
            count += 1

        logger.warning('Validating inputs. Please wait, may take a few moments')
        df = pd.DataFrame(data)
        logger.debug(f'\nIncoming data\n{df}')

        if by == 'hostname':
            waf = self.populate_waf_data(by, df)
        else:
            df.insert(0, 'property_version', '')
            df.insert(0, 'property_id', '')
            all_property = df.property_name.unique()
            logger.debug(all_property)

            # validate property
            invalid_property = []
            for property in all_property:
                if wrap_api.property_exists(property) is False:
                    invalid_property.append(property)
                else:
                    property_df = pd.DataFrame(wrap_api.get_property_id(property))
                    if not activate:
                        new_df = property_df[property_df['stagingStatus'] == 'ACTIVE']
                    else:
                        if network == 'staging':
                            new_df = property_df[property_df['stagingStatus'] == 'ACTIVE']
                        else:
                            new_df = property_df[property_df['productionStatus'] == 'ACTIVE']

                    if new_df.empty:
                        sys.exit(logger.error(f'property {property} must be activated on the {network.upper()} network first'))
                    property_id = new_df['propertyId'].values[0]
                    df.loc[df['property_name'] == property, 'property_id'] = property_id
                    df.loc[df['property_name'] == property, 'property_version'] = new_df['propertyVersion'].values[0]

            # only process valid properties
            if len(invalid_property) == 0:
                valid_property = all_property
            else:
                logger.error(f'invalid property name {invalid_property}')
                valid_property = list(set(all_property) - set(invalid_property))
                logger.debug(f'{valid_property=}')
            df = df[df['property_name'].isin(valid_property)]
            columns = ['property_name', 'waf_config_name', 'waf_policy_name', 'hostname', 'property_id', 'property_version']
            df.sort_values(by=['waf_config_name', 'property_name'], inplace=True)
            df.reset_index(drop=True, inplace=True)
            logger.debug(f'\nCleanup Round 1\n{df[columns]}')

            # populate remaining empty hostname
            if 'hostname' in df.columns:
                if not activate:
                    network = 'staging'
                df['waf_target_hostname'] = df[['property_id', 'hostname']].apply(lambda x: [] if x.hostname is None else x.hostname, axis=1)
                if 'waf_target_hostname' in df.columns:
                    columns.append('waf_target_hostname')
                    df['waf_target_hostname'] = df['waf_target_hostname'].apply(lambda x: self.stringToList(x))
                df['hostname'] = df[['property_id', 'hostname']].apply(
                    lambda x: wrap_api.get_property_hostnames(x.property_id, contract_id, group_id, network) if x.hostname is None
                    else x.hostname, axis=1)
                df['hostname'] = df['hostname'].apply(lambda x: self.stringToList(x))
                logger.debug(f'\nCleanup Round 2\n{df[columns]}')
            else:
                df.insert(0, 'hostname', '')
                hostnames = wrap_api.get_property_hostnames(property_id, contract_id, group_id, network)
                df.loc[df['property_name'] == property, 'hostname'] = df['hostname'].apply(lambda x: hostnames)

        # processing by name of WAF Security Configuration
        # logger.info('Main data')
        # print(tabulate(df[['property_id', 'waf_config_name', 'waf_policy_name', 'waf_target_hostname']], headers='keys', tablefmt='psql', showindex=True))
        waf = self.populate_waf_data(by, df)
        df = pd.DataFrame(waf)
        waf_df = df.set_index('waf_config_name')
        waf_df.fillna('', inplace=True)
        logger.debug(f'\nPivot\n{waf_df}')

        # display data on terminal
        indexes = waf_df.index.to_list()
        columns = waf_df.columns.to_list()
        df = pd.DataFrame(waf, index=indexes, columns=columns)
        show_df = df.stack()
        show_df = pd.DataFrame(df.stack()).reset_index()
        logger.debug(f'\n{show_df}')
        show_df.columns = ['waf_config_name', 'policy', 'hostname']
        show_df[['hostname', 'waf_target_hostname']] = pd.DataFrame(show_df['hostname'].tolist(), index=show_df.index)
        logger.debug(f'\n{show_df}')
        if by == 'propertyname':
            columns = ['waf_config_name', 'policy', 'waf_target_hostname']
        else:
            columns = ['waf_config_name', 'policy', 'hostname']
        logger.info(f'\n{show_df[columns].to_markdown(headers=columns, tablefmt="psql")}')

        # check duplicate waf config name
        all_waf = show_df['waf_config_name'].unique().tolist()
        for waf in all_waf:
            config_detail = self.getWafConfigIdByName(wrap_api, waf)
            if config_detail['Found']:
                count += 1
                logger.error(f'{waf}{space:>{column_width - len(waf)}}duplicate waf_config_name already exists')

        # check if hostnames are activated in another config
        # TODO: is this possible on staging?

        _, selectable_hostnames, _ = wrap_api.get_selectable_hostnames(contract_id[4:], group_id[4:], network)
        all_hostnames = sorted(list({host for hosts in show_df['hostname'].tolist() for host in hosts}))
        logger.debug(all_hostnames)
        for hostname in all_hostnames:
            if hostname not in selectable_hostnames:
                count += 1
                logger.error(f'{hostname}{space:>{column_width - len(hostname)}}invalid hostname for contract/group')

        if main_object.network:
            if not self.validate_email(main_object.notification_emails):
                count += 1

        if count == 0:
            self.valid is True
        else:
            self.valid is False
            sys.exit(logger.error(f'Total {count} errors, please review'))

        return show_df

    def env_validator(self, filepath: str) -> dict:
        env_details = {}
        try:
            with open(filepath) as f:
                env_details = json.load(f)
        except FileNotFoundError as err:
            logger.error(err)
        return env_details

    def search_for_json_rule_by_name(self, data, target_key, target_value, path='', paths=None):
        if paths is None:
            paths = []

        if isinstance(data, dict):
            for key, value in data.items():
                new_path = f'{path}.{key}' if path else key
                if key == target_key and value == target_value:
                    paths.append(new_path)
                if isinstance(value, (dict, list)):
                    self.search_for_json_rule_by_name(
                        value, target_key, target_value, new_path, paths
                    )
        elif isinstance(data, list):
            for index, item in enumerate(data):
                new_path = f'{path}[{index}]'
                self.search_for_json_rule_by_name(item, target_key, target_value, new_path, paths)

        return paths

    def create_new_rule_json(self, onboard: Onboard, cpcode: dict[str, int], ruletree: dict):

        if onboard.ruletree_https_paths_rules_loc:
            path_inj_loc = onboard.ruletree_https_paths_rules_loc.replace('.name', '')
            full_ruleset = self.get_full_behavior_by_jsonpath(ruletree, path_inj_loc)

            for criteria in full_ruleset['criteria']:
                if criteria['name'] == 'path':
                    logger.warning('Found path match criteria')
                    for path in onboard.paths:
                        criteria['options']['values'].append(path['path_match'])
                    logger.warning(f'Added {len(onboard.paths)} paths to {onboard.property_https_paths_rule_name} rule')
                    break

        loc = onboard.ruletree_rules_loc.replace('.name', '')
        full_ruleset = self.get_full_behavior_by_jsonpath(ruletree, loc)
        logger.warning(f'Created new {len(onboard.paths)} rules')
        for rule in onboard.paths:
            json_to_add = self.generate_custom_rule_json(cpcode[rule['rulename']], rule['path_match'], rule['rulename'].lstrip(' '))
            full_ruleset['children'].append(json_to_add)
            logger.info(f"    rulename {rule['rulename']:<20} {rule['path_match']}")
        full_ruleset = sorted(full_ruleset['children'], key=lambda x: x['name'].lstrip(' '))
        parts = re.split(r'\.|\[|\]', f'{loc}.children')
        # Filter out empty strings from results
        parts = [part for part in parts if part]

        current = ruletree
        for i, part in enumerate(parts):
            if part.isdigit():  # This is a list index
                part = int(part)  # Convert index to integer
                if i == len(parts) - 1:
                    current[part] = full_ruleset  # Set value at index
                else:
                    current = current[part]  # Navigate to the next level
            else:  # This is a dictionary key
                if i == len(parts) - 1:
                    current[part] = full_ruleset  # Set new value at the key
                else:
                    current = current.get(part, {})  # Navigate to the next level, creating new dict if necessary
        return ruletree

    def get_full_behavior_by_jsonpath(self, json_object, json_path: str):
        """
        Extracts a value from a nested JSON object using a simplified JSONPath expression.

        Parameters:
        json_object (dict): The JSON object to be queried.
        json_path (str): The simplified JSONPath expression to locate the desired data.

        Returns:
        The value from the JSON object located at the specified path.
        If the path does not exist, None is returned.
        """
        elements = json_path.split('.')
        current_element = json_object

        for elem in elements:
            if '[' in elem and ']' in elem:
                key, index = elem.split('[')
                index = int(index[:-1])  # Remove ']' and convert to int
                try:
                    if key == '':
                        current_element = current_element[index]
                    else:
                        current_element = current_element[key][index]
                except (IndexError, KeyError, TypeError):
                    return None
            else:
                try:
                    current_element = current_element[elem]
                except KeyError:
                    return None

        return current_element

    def generate_custom_rule_json(self, cpcode: int, path: str, rulename: str):

        return ({
            'name': rulename.upper(),
            'children': [],
            'behaviors': [
                {
                    'name': 'cpCode',
                    'options': {
                        'value': {
                            'id': cpcode
                        }
                    }
                },
                {
                    'name': 'setVariable',
                    'options': {
                        'variableName': 'PMUSER_CURATED_PROP',
                        'valueSource': 'EXPRESSION',
                        'transform': 'NONE',
                        'variableValue': '1'
                    }
                }
            ],
            'criteria': [
                {
                    'name': 'path',
                    'options': {
                        'matchOperator': 'MATCHES_ONE_OF',
                        'values': [
                            path
                        ],
                        'matchCaseSensitive': True,
                        'normalize': False
                    }
                }
            ],
            'criteriaMustSatisfy': 'any',
            'comments': f'RARE-JIRA: Add Curated property {rulename.upper()}'
        })

    def check_existing_custom_rules(self, onboard, ruletree) -> list:
        loc = onboard.ruletree_rules_loc.replace('.name', '')
        full_ruleset = self.get_full_behavior_by_jsonpath(ruletree, loc)
        input_rule_names = set(list(map(lambda x: x['rulename'], onboard.paths)))
        existing_rule_names = set(list(map(lambda x: x['name'], full_ruleset['children'])))
        duplicated = sorted(list(existing_rule_names.intersection(input_rule_names)))
        return duplicated


class Cloudlets:
    def __init__(self, config):
        self.account_key = config.account_key
        self.section = config.section
        self.edgerc = config.edgerc
        self.policy_name = None

    def build_cmd(self) -> str:
        cmd = 'akamai cloudlets'
        if self.account_key:
            cmd = f'{cmd} -a {self.account_key}'
        if self.edgerc:
            cmd = f'{cmd} --edgerc {self.edgerc}'
        if self.section:
            cmd = f'{cmd} -s {self.section}'
        return cmd

    def split_into_chunks(self, match_value, new_value, limit: int):
        data = match_value.split(' ')
        new_values = new_value.split(' ')
        data.extend(new_values)
        sorted_paths = sorted(data)
        chunks = []
        current_chunk = ''
        for i, value in enumerate(sorted_paths):
            if len(current_chunk) + len(value) + 1 <= limit:
                current_chunk += f'{value} '
            else:
                chunks.append(current_chunk.strip())
                current_chunk = f'{value} '
        if current_chunk:
            chunks.append(current_chunk.strip())

        return chunks

    def validate_cloudlet_policy(self, policy: str) -> bool:
        cmd = self.build_cmd()
        cmd = f'{cmd} status --policy {policy}'
        command = cmd.split(' ')
        print()
        logger.warning('Validating cloudlet policy')
        childprocess = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False)
        stdout, _ = childprocess.communicate()
        if 'Found policy-id' in stdout.decode('utf-8'):
            self.policy_name = policy
            logger.info(stdout.decode('utf-8'))
            return True

        logger.error(f'Unable to find existing policy {policy}')
        return False

    def retrieve_matchrules(self, policy: str) -> bool:
        cmd = self.build_cmd()
        cmd = f'{cmd} retrieve --only-match-rules --json --policy {policy}'
        command = cmd.split(' ')
        logger.debug(cmd)
        childprocess = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False)
        stdout, stderr = childprocess.communicate()
        if 'policy_matchrules.json' not in stdout.decode('utf-8'):
            sys.exit(logger.info(stdout.decode('utf-8')))
        return True

    def create_cloudlet_policy_version(self, policy: str, new_rule: dict, notes: str) -> int:
        updated_rules = {}
        updated_rules['matchRuleFormat'] = '1.0'
        updated_rules['matchRules'] = new_rule

        with open('policy_matchrules_updated.json', 'w') as f:
            json.dump(updated_rules, f, indent=4)

        cmd = self.build_cmd()
        cmd = f'{cmd} update --policy {policy} --file policy_matchrules_updated.json --notes'
        command = cmd.split(' ')
        command.append(notes)  # notes can have space inside
        full_command_str = ' '.join(command)
        #print(f"🛠️ Executing Cloudlet command: {full_command_str}")
        #logger.debug(f"Executing Cloudlet command: {full_command_str}")
        update_cloudlet_cli = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        stdout, stderr = update_cloudlet_cli.communicate()
        version_number = 0
        if 'new version ' not in stdout.decode('utf-8'):
            sys.exit(logger.error(stdout.decode('utf-8')))
        else:
            create_output = stdout.decode('utf-8')
            logger.debug(create_output)
            pattern = r'version (\d+)'
            match = re.search(pattern, create_output)
            if match:
                print()
                version_number = match.group(1)
                logger.warning(f'cloudlet new version number: v{version_number}')
        return version_number

    def activate_policy(self, onboard, version: int, network: str):
        if network == 'STAGING':
            activation = onboard.activate_cloudlet_staging
        else:
            activation = onboard.activate_cloudlet_production

        if not activation:
            logger.warning(f'SKIP - Activate Cloudlet on {network.upper()}')
        else:
            cmd = self.build_cmd()
            cmd = f'{cmd} activate --policy {onboard.cloudlet_policy} --network staging --version {version}'
            command = cmd.split(' ')
            logger.debug(cmd)
            act_cloudlet_cli = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            stdout, stderr = act_cloudlet_cli.communicate()
            print(stdout.decode('utf-8'))

    def activate_policy_for_customdelete(self, onboard, version: int, network: str):
        activation = False
        if network == 'STAGING':
            activation = onboard.activate_cloudlet_staging
        if network == 'PRODUCTION':
            activation = onboard.activate_cloudlet_production

        if not activation:
            logger.warning(f'SKIP - Activate Cloudlet on {network.upper()}')
        else:
            cmd = self.build_cmd()
            cmd = f'{cmd} activate --policy {onboard.cloudlet_policy} --network  {network.lower()} --version {version}'
            command = cmd.split(' ')
            logger.debug(cmd)
            act_cloudlet_cli = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            stdout, stderr = act_cloudlet_cli.communicate()
            print(stdout.decode('utf-8'))
            logger.warning(f'Successfully activated Cloudlet configuration to Akamai {network} network')  


    def update_phasedrelease_rule(self,
                                  rules: dict,
                                  rulename: str,
                                  new_value: str,
                                  matchon_type: str | None = None):
        print("🚨 Entered update_phasedrelease_rule()")
        original_property = []
        update = False
        index = 0
        match_rules = rules['matchRules']
        before_match_rules = deepcopy(match_rules)
        for i, rule in enumerate(match_rules, start=1):
            name = rule['name']
            if name != rulename:
                continue

            index = i
            try:
                matches = rule['matches']
            except KeyError:
                continue
            origin = rule['forwardSettings']['originId']
            percent = rule['forwardSettings']['percent']
            print("🚨 Entered update_phasedrelease_rule()....1")
            for j, element in enumerate(matches, start=1):
                match_value = element['matchValue']
                match_operator = element['matchOperator']
                match_type = element['matchType']
                negative_match = element['negate']
                xs = match_value.split(' ')
                print("🚨 Entered update_phasedrelease_rule()..2")
                str_count = len(match_value)
                elements = len(xs)
                msg = f'{name:<15} {i:>3}.{j}   {match_type:<8} {str(negative_match):<10}'
                options = f'{percent}%  {origin}'
                logger.info(f'   {msg} {match_operator:<10} {str_count:<7} {elements:<5} {options}')

                LIMIT = 8000
                if not negative_match:
                    original_property.append(element)
                else:
                    if not match_value.startswith('/en/hotels/'):
                        original_property.append(element)
                        continue

                    if len(match_value) > LIMIT:
                        original_property.append(element)
                        continue

                    print()
                    chunks = self.split_into_chunks(match_value, new_value, LIMIT)

                    for ni, chunk in enumerate(chunks, start=1):
                        update = True
                        if ni == 1:
                            ex = deepcopy(element)
                            ex['matchValue'] = chunk
                            original_property.append(ex)
                            logger.info(f'Update criteria {i:>3}.{j} {match_operator:<10} {len(chunk)}')

                        else:
                            ex = deepcopy(element)
                            ex['matchValue'] = chunk
                            original_property.append(ex)
                            j = j + 1
                            logger.info(f'New    criteria {i:>3}.{j} {match_operator:<10} {len(chunk)}')
        if update:
            msg = f"{len(before_match_rules[index - 1]['matches'])}/{len(original_property)}"
            logger.warning(f'Number of condition for rule {rulename} before/after: {msg}')
            print()

            match_rules[index - 1]['matches'] = original_property
            for i, match in enumerate(match_rules[index - 1]['matches'], start=1):
                str_count = len(match['matchValue'])
                xs = match['matchValue'].split(' ')
                elements = len(xs)
                logger.info(f'{i:<3} {str_count:<10} {elements:<5}')

        return update, match_rules
    
    def remove_phasedrelease_paths(self,cloudlet_rules: dict, onboard) -> tuple[bool, dict]:
        """
        Remove path match entries from a Cloudlet rule named 'Property'.
        Returns a tuple (updated_flag, updated_rules_dict).
        """
        path_matches_to_remove = set(map(lambda x: x['path_match'].strip(), onboard.paths))
        logger.debug(f"Calling remove_phasedrelease_paths with paths: {path_matches_to_remove}")

        for rule in cloudlet_rules.get('matchRules', []):
            if rule['name'].strip().lower() == 'property':
                matches = rule.get('matches', [])
                new_matches = []
                removed_any = False

                for match in matches:
                    if match.get('matchType') == 'path':
                        values = match.get('matchValue', '').split()
                        kept_values = [v for v in values if v.strip() not in path_matches_to_remove]
                        if len(kept_values) != len(values):
                            removed_any = True
                            #logger.warning(f"Removing path(s): {set(values) - set(kept_values)}")
                        if kept_values:
                            match['matchValue'] = ' '.join(kept_values)
                            new_matches.append(match)
                    else:
                        new_matches.append(match)

                rule['matches'] = new_matches
                updated_rules = {
                    "matchRuleFormat": "1.0",
                    "matchRules": cloudlet_rules.get("matchRules", [])
                }

                logger.debug(f"Value of 'updated' after removal attempt: {removed_any}")
                logger.debug(json.dumps(updated_rules, indent=2))
                return removed_any, updated_rules

        logger.warning('Rule "Property" not found in Cloudlet Policy')
        return False, {}

