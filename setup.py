import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="ewah", # Replace with your own username
    version="0.2.0",
    author="Bijan Soltani",
    author_email="bijan.soltani+ewah@gemmaanalytics.com",
    description="An ELT with airflow helper module: Ewah",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://gemmaanalytics.com/",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
    ],
    python_requires='>=3.6',
    install_requires=[
          'pyyaml',
          'psycopg2',
          'snowflake-connector-python',
          'pytz',
          # don't force installation of packages related to operators
          # they can either be installed manually or not at all when using
          # kubernetes executor, thereby avoiding any dependency conflicts
          # 'gspread',
          # 'yahoofinancials',
          # 'google-api-python-client',
          # 'oauth2client',
          # 'cx_Oracle',
          # 'facebook_business',
          # 'mysql-connector-python',
          # 'pymongo',
      ],
)
