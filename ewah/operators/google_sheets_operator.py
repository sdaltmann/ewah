from ewah.operators.base_operator import EWAHBaseOperator
from ewah.ewah_utils.airflow_utils import airflow_datetime_adjustments
from ewah.constants import EWAHConstants as EC

from airflow.hooks.base_hook import BaseHook

import gspread
import json
from oauth2client.service_account import ServiceAccountCredentials as SAC

class EWAHGSpreadOperator(EWAHBaseOperator):

    _IS_INCREMENTAL = False
    _IS_FULL_REFRESH = True

    _REQUIRES_COLUMNS_DEFINITION = True

    def _translate_alphanumeric_column(self, column_identifier):
        if type(column_identifier) == str:
            column_number = 0
            i = 0
            ident_dict = {}
            while column_identifier:
                letter = column_identifier[-1:].lower()
                if ord(letter) > ord('z') or ord(letter) < ord('a'):
                    raise Exception('Column letter {0} out of bounds!'.format(
                        letter,
                    ))
                column_identifier = column_identifier[:-1]
                ident_dict.update({i: ord(letter) + 1 - ord('a')})
                i += 1
            return sum([v * (26 ** k) for k, v in ident_dict.items()])
        else:
            return column_identifier

    def __init__(
        self,
        workbook_key, # can be seen in the URL of the workbook
        sheet_key, # name of the worksheet
        start_row=2, # in what row does the data begin?
        end_row=None, # optional: what is the last row? None gets all data
    *args, **kwargs):
        super().__init__(*args, **kwargs)

        credentials = BaseHook.get_connection(self.source_conn_id).extra_dejson
        if not credentials.get('client_secrets'):
            raise Exception('Google Analytics Credentials misspecified!' \
                + ' Example of a correct specifidation: {0}'.format(
                    json.dumps({"client_secrets":{
                        "type": "service_account",
                        "project_id": "abc-123",
                        "private_key_id": "123456abcder",
                        "private_key": "-----BEGIN PRIVATE KEY-----\nxxx\n-----END PRIVATE KEY-----\n",
                        "client_email": "xyz@abc-123.iam.gserviceaccount.com",
                        "client_id": "123457",
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                        "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/xyz%40abc-123.iam.gserviceaccount.com"
                    }})
                ))

        column_match = {}
        for col_key, col_def in self.columns_definition.items():
            if (not col_def) or (not col_def.get(EC.QBC_FIELD_GSHEET_COLNO)):
                raise Exception((
                        'Column {0} is missing information regarding the ' \
                        + 'position of the column in the sheet.'
                    ).format(col_key)
                )
            column_match.update({
                self._translate_alphanumeric_column(
                    col_def[EC.QBC_FIELD_GSHEET_COLNO],
                ): col_key,
            })

        self.client_secrets = credentials['client_secrets']
        self.column_match = column_match
        self.workbook_key = workbook_key
        self.sheet_key = sheet_key
        self.start_row = start_row
        self.end_row = end_row

    def ewah_execute(self, context):
        client = gspread.authorize(
            SAC.from_json_keyfile_dict(
                self.client_secrets,
                ['https://spreadsheets.google.com/feeds'],
            ),
        )

        self.log.info('Retrieving data...')
        workbook = client.open_by_key(self.workbook_key)
        sheet = workbook.worksheet(self.sheet_key)
        raw_data = sheet.get_all_values()[self.start_row-1:self.end_row]

        # Load the data from the sheet into a format for upload into the DWH
        data = []
        for row in raw_data: # Iterate through each row
            data_dict = {} # New row, new dictionary of field:value
            row_is_null = True # Is entire row empty?
            for position, column in self.column_match.items():
                datapoint = row[position-1]
                data_dict.update({column:datapoint})
                if row_is_null:
                    if bool(datapoint):
                        if not (datapoint == '0'):
                            # Special case with zeroes in text format
                            row_is_null = False # Row is not empty!
            if not row_is_null: # Ignore empty rows
                data.append(data_dict)

        self.upload_data(data)
