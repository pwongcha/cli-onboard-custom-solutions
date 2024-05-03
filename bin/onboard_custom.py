from __future__ import annotations

import os
from pathlib import Path


class Onboard:
    def __init__(self, config, click_args):
        try:
            self.version_notes = 'adding paths via cli'
            self.property_name = []
            self.csv_loc = self.get_actual_location(click_args['csv'])
            self.env_loc = self.get_actual_location(click_args['env'])
            self.build_env = click_args['build_env']
            self.valid_csv = True
            self.valid_env = True
            self.paths = []
            self.property_version = click_args['property_version']

            if 'delivery-staging' in click_args['activate']:
                self.activate_property_staging = True
            if 'waf-staging' in click_args['activate']:
                self.activate_waf_policy_staging = True

            if 'delivery-production' in click_args['activate']:
                self.activate_property_production = True
            if 'waf-production' in click_args['activate']:
                self.activate_waf_policy_production = True

            if click_args['email']:
                self.notification_emails = click_args['email']
            else:
                self.notification_emails = ['noreply@akamai.com']

            self.version_notes = 'Created using Onboard CLI'

            # Read config object that contains the command line parameters
            if not config.edgerc:
                if not os.getenv('AKAMAI_EDGERC'):
                    self.edgerc = os.path.join(os.path.expanduser('~'), '.edgerc')
                else:
                    self.edgerc = os.getenv('AKAMAI_EDGERC')
            else:
                self.edgerc = config.edgerc

            if not config.section:
                if not os.getenv('AKAMAI_EDGERC_SECTION'):
                    self.section = 'onboard'
                else:
                    self.section = os.getenv('AKAMAI_EDGERC_SECTION')
            else:
                self.section = config.section

        except KeyError as k:
            print('\nMissing argument ' + str(k))
            exit(-1)

    def get_actual_location(self, file_location: str) -> str:
        abs_file_location = file_location
        home = str(Path.home())
        if '~' in file_location:
            file_location = file_location.replace('~', '')
            abs_file_location = f'{home}/{file_location}'

        return abs_file_location
