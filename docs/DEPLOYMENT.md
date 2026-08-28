# AWS Elastic Beanstalk demo deployment

This project can be deployed as a short-lived single-instance Flask demonstration on AWS Elastic Beanstalk. Elastic Beanstalk itself has no separate service fee, but it creates resources such as EC2 and EBS that can incur charges. Delete the environment after assessment.

## Prepared files

- `application.py` exposes the Flask `application` callable expected by Elastic Beanstalk.
- `Procfile` starts Gunicorn on port 8000.
- `.ebignore` excludes local databases, tests, documents, virtual environments and work files.
- `/health` confirms that the application and SQLite database are responding.
- Production mode requires a non-default `SECRET_KEY` and enables secure cookies.

## Deploy

1. Authenticate with short-lived AWS credentials:

   ```powershell
   aws login
   aws sts get-caller-identity
   ```

2. Initialise the application in the closest configured region:

   ```powershell
   eb init -p python-3.11 equiptrack-school-loans --region us-east-1
   ```

3. Create a strong random secret and a single-instance environment:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   eb create equiptrack-demo --single --instance-type t3.micro
   eb setenv APP_ENV=production SECRET_KEY=<paste-generated-value>
   eb deploy
   eb status
   ```

4. Open the reported Elastic Beanstalk URL and verify `/health` returns `{"status":"healthy"}`.

## Important limitation

SQLite is suitable for this short assessment demonstration, but the database is stored on the environment instance. Rebuilding or replacing the instance can lose data. A real school deployment should use an approved managed database, HTTPS/custom domain, school single sign-on, backups, monitoring and a retention policy.

## Cleanup after marking

```powershell
eb terminate equiptrack-demo
```

Confirm the environment is terminated in the AWS console so EC2/EBS charges do not continue.
