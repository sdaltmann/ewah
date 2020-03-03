from airflow import DAG

from airflow.operators.postgres_operator import PostgresOperator
from airflow.operators.bash_operator import BashOperator
from airflow.sensors.sql_sensor import SqlSensor
from airflow.hooks.base_hook import BaseHook
from airflow.configuration import conf
from airflow.models import Variable

from ewah.constants import EWAHConstants as EC

from datetime import datetime, timedelta
import os

def dbt_dag_factory(
    dag_name,
    dwh_engine,
    dwh_conn_id,
    dbt_project_folder, # e.g. ~/dbt_home/analytics
    dbt_venv_folder, # e.g. ~/dbt_home/env
    dbt_profiles_dir=None, # defaults to dbt_project_folder
    env=None, # dict of env vars for profiles.yml, created on the spot if None
    is_full_refresh=False, # use dbt's --full-refresh flag
    models=[], # list of models, as per dbt syntax (e.g. tag, +, @ are allowed)
    exclude=[], # list of models to exclude, as per dbt syntax
    default_args={}, # default_args for DAG
    start_date=None,
    schedule_interval=None,
    seed=True,
    run=True,
    test=True,
    docs=True,
):
    if not (seed or run or test or docs):
        raise Exception('Must run at least one of seed, run, test, or docs!')

    dbt_profiles_dir = dbt_profiles_dir or dbt_project_folder

    if models:
        models_flag = '--models ' + ' '.join(models)
    else:
        models_flag = ''

    if exclude:
        exclude_flag = '--exclude ' + ' '.join(exclude)
    else:
        exclude_flag = ''

    if is_full_refresh:
        fr_flag = '--full-refresh'

    flags_run = ' '.join([fr_flag, exclude_flag, models_flag])
    flags_test = '{0} {1}'.format(exclude_flag, models_flag)

    illegal_strings = [
        '&&', '\n', '|'
    ]
    for ill_str in illegal_strings:
        for cmd_str in [dbt_venv_folder, dbt_project_folder, flags]:
            if ill_str in cmd_str:
                raise Exception('Illegal character {0} found in {1}!'.format(
                    ill_str,
                    cmd_str,
                ))

    bash_command = '''
        source {0}/bin/activate
        cd {1}
        dbt {2}
    '''.format(
        dbt_venv_folder,
        dbt_project_folder,
        '{0}',
    )

    if not env:
        conn = BaseHook.get_connection(dwh_conn_id)
        if dwh_engine == EC.DWH_ENGINE_POSTGRES:
            env = {
                'DBT_DWH_HOST': str(conn.host),
                'DBT_DWH_USER': str(conn.login),
                'DBT_DWH_PASS': str(conn.password),
                'DBT_DWH_PORT': str(conn.port),
                'DBT_DWH_DBNAME': str(conn.schema),
                'DBT_DWH_SCHEMA': dbt_schema_name,
                'DBT_PROFILES_DIR': folder,
            }
        elif dwh_engine == EC.DWH_ENGINE_SNOWFLAKE:
            extra = conn.extra_dejson
            env = {
                'DBT_ACCOUNT': extra.get('account', conn.host),
                'DBT_USER': conn.login,
                'DBT_PASS': conn.password,
                'DBT_ROLE': extra.get('role'),
                'DBT_DB': extra.get('database'),
                'DBT_WH': extra.get('warehouse'),
                'DBT_PROFILES_DIR': folder,
            }
        else:
            raise ValueError('DWH type not implemented!')

    dag = DAG(
        dag_name,
        catchup=False,
        max_active_runs=1,
        schedule_interval=schedule_interval,
        start_date=start_date,
        default_args=default_args,
    )

    previous_task = None
    current_task = None

    if seed:
        previous_task = BashOperator(
            task_id='dbt_seed',
            bash_command=bash_command.format('seed {0}'.format(fr_flag)),
            env=env,
            dag=dag,
        )

    if run:
        current_task = BashOperator(
            task_id='dbt_run',
            bash_command=bash_command.format('run {0}'.format(flags_run)),
            env=env,
            dag=dag,
        )
        if previous_task:
            previous_task >> current_task
        previous_task = current_task

    if test:
        current_task = BashOperator(
            task_id='dbt_test',
            bash_command=bash_command.format('test {0}'.format(flags_test)),
            env=env,
            dag=dag,
        )
        if previous_task:
            previous_task >> current_task
            previous_task = current_task

    if docs:
        current_task = BashOperator(
            task_id='dbt_docs_generate',
            bash_command=bash_command.format('docs generate'),
            env=env,
            dag=dag,
        )
        if previous_task:
            previous_task >> current_task
        previous_task = current_task


def dbt_dags_factory(
    dwh_engine,
    dwh_conn_id,
    project_name,
    dbt_schema_name,
    airflow_conn_id,
    analytics_reader=None, # list of users of DWH who are read-only
    schedule_interval=timedelta(hours=1),
    start_date=datetime(2019,1,1),
    default_args=None,
    folder=None,
):

    if analytics_reader:
        for statement in ('insert', 'update', 'delete',
            'drop', 'create', 'select', ';', 'grant'):
            for reader in analytics_reader:
                if (statement in reader.lower()):
                    raise Exception('Error! The analytics reader {0} ' \
                        + 'is invalid.'.format(reader))

        #analytics_reader = analytics_reader.split(',')
        analytics_reader_sql = f'\nGRANT USAGE ON SCHEMA "{dbt_schema_name}"'
        analytics_reader_sql += ' TO "{0}";'
        analytics_reader_sql += f'''
        \nGRANT SELECT ON ALL TABLES IN SCHEMA "{dbt_schema_name}"''' + ' TO "{0}";'
        analytics_reader_sql = ''.join([
            analytics_reader_sql.format(i) for i in analytics_reader
        ])



    dag = DAG(
        'DBT_run',
        catchup=False,
        max_active_runs=1,
        schedule_interval=schedule_interval,
        start_date=start_date,
        default_args=default_args,
    )

    dag_full_refresh = DAG(
        'DBT_run_full_refresh',
        catchup=False,
        max_active_runs=1,
        schedule_interval=None,
        start_date=start_date,
        default_args=default_args,
    )

    folder = folder or (os.environ.get('AIRFLOW_HOME') or conf.get('core', 'airflow_home')).replace(
        'airflow_home/airflow',
        'dbt_home',
    )

    bash_command = '''
    cd {1}
    source env/bin/activate
    cd {2}
    dbt {0}
    '''.format(
        '{0}',
        folder,
        project_name,
    )

    sensor_sql = """
        SELECT
            CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END -- only run if exatly equal to 0
        FROM public.dag_run
        WHERE dag_id IN ('{0}', '{1}')
        and state = 'running'
        and not (run_id = '{2}')
    """.format(
        dag._dag_id,
        dag_full_refresh._dag_id,
        '{{ run_id }}',
    )

    # refactor?! not coupled to values in profiles.yml!
    if dwh_engine == EC.DWH_ENGINE_POSTGRES:
        conn = BaseHook.get_connection(dwh_conn_id)
        env = {
            'DBT_DWH_HOST': str(conn.host),
            'DBT_DWH_USER': str(conn.login),
            'DBT_DWH_PASS': str(conn.password),
            'DBT_DWH_PORT': str(conn.port),
            'DBT_DWH_DBNAME': str(conn.schema),
            'DBT_DWH_SCHEMA': dbt_schema_name,
            'DBT_PROFILES_DIR': folder,
        }
    elif dwh_engine == EC.DWH_ENGINE_SNOWFLAKE:
        analytics_conn = BaseHook.get_connection(dwh_conn_id)
        analytics_conn_extra = analytics_conn.extra_dejson
        env = {
            'DBT_ACCOUNT': analytics_conn_extra.get(
                'account',
                analytics_conn.host,
            ),
            'DBT_USER': analytics_conn.login,
            'DBT_PASS': analytics_conn.password,
            'DBT_ROLE': analytics_conn_extra.get('role'),
            'DBT_DB': analytics_conn_extra.get('database'),
            'DBT_WH': analytics_conn_extra.get('warehouse'),
            'DBT_PROFILES_DIR': folder,
        }
    else:
        raise ValueError('DWH type not implemented!')

    #with dag:
    snsr = SqlSensor(
        task_id='sense_dbt_conflict_avoided',
        conn_id=airflow_conn_id,
        sql=sensor_sql,
        dag=dag,
    )

    dbt_seed = BashOperator(
        task_id='run_dbt_seed',
        bash_command=bash_command.format('seed'),
        env=env,
        dag=dag,
    )

    dbt_run = BashOperator(
        task_id='run_dbt',
        bash_command=bash_command.format('run'),
        env=env,
        dag=dag,
    )

    dbt_test = BashOperator(
        task_id='test_dbt',
        bash_command=bash_command.format('test'),
        env=env,
        dag=dag,
    )

    dbt_docs = BashOperator(
        task_id='create_dbt_docs',
        bash_command=bash_command.format('docs generate'),
        env=env,
        dag=dag,
    )

    snsr >> dbt_seed >> dbt_run >> dbt_test

    if analytics_reader:
        # This should not occur when using Snowflake
        read_rights = PostgresOperator(
            task_id='grant_access_to_read_users',
            sql=analytics_reader_sql,
            postgres_conn_id=dwh_conn_id,
            dag=dag,
        )
        dbt_test >> read_rights >> dbt_docs
    else:
        dbt_test >> dbt_docs


    #with dag_full_refresh:
    snsr = SqlSensor(
        task_id='sense_dbt_conflict_avoided',
        conn_id=airflow_conn_id,
        sql=sensor_sql,
        dag=dag_full_refresh,
    )

    dbt_seed = BashOperator(
        task_id='run_dbt_seed',
        bash_command=bash_command.format('seed'),
        env=env,
        dag=dag_full_refresh,
    )

    dbt_run = BashOperator(
        task_id='run_dbt',
        bash_command=bash_command.format('run --full-refresh'),
        env=env,
        dag=dag_full_refresh,
    )

    dbt_test = BashOperator(
        task_id='test_dbt',
        bash_command=bash_command.format('test'),
        env=env,
        dag=dag_full_refresh,
    )

    dbt_docs = BashOperator(
        task_id='create_dbt_docs',
        bash_command=bash_command.format('docs generate'),
        env=env,
        dag=dag_full_refresh,
    )

    snsr >> dbt_seed >> dbt_run >> dbt_test

    if analytics_reader:
        read_rights = PostgresOperator(
            task_id='grant_access_to_read_users',
            sql=analytics_reader_sql,
            postgres_conn_id=dwh_conn_id,
            dag=dag_full_refresh,
        )
        dbt_test >> read_rights >> dbt_docs
    else:
        dbt_test >> dbt_docs
    return (dag, dag_full_refresh)
