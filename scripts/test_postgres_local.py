"""Repeatable disposable PostgreSQL integration test. Requires Docker and OpenSSL."""
import json
import pathlib
import subprocess
import tempfile
import time
import uuid


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    repo=pathlib.Path(__file__).resolve().parents[1]
    name='partner-pg-test-'+uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix=name) as folder:
        root=pathlib.Path(folder)
        (root/'openssl.cnf').write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=pg-test\n[ext]\nsubjectAltName=DNS:pg-test\nbasicConstraints=critical,CA:TRUE\n')
        run('openssl','req','-x509','-nodes','-newkey','rsa:2048','-days','1',
            '-keyout',str(root/'key.pem'),'-out',str(root/'cert.pem'),'-config',str(root/'openssl.cnf'),capture_output=True)
        env={'DB_HOST':'pg-test','POSTGRES_DISPOSABLE_TEST':'true'}
        for key,user in [('ADMIN_CREDENTIAL','agent_admin'),('MIGRATION_CREDENTIAL','agent_migrator'),('RUNTIME_CREDENTIAL','agent_runtime')]:
            env[key]=json.dumps({'username':user,'password':'disposable-fixture-only'})
        (root/'fixture.env').write_text('\n'.join(k+'='+v for k,v in env.items())+'\n')
        run('docker','build','--platform','linux/amd64','-f',str(repo/'infra/Dockerfile.database'),'-t',name,str(repo))
        run('docker','network','create',name)
        try:
            run('docker','run','-d','--name',name,'--network',name,'--network-alias','pg-test',
                '-e','POSTGRES_PASSWORD=disposable-fixture-only','-e','POSTGRES_DB=partneragent',
                '-v',str(root)+':/fixtures:ro','postgres:17','bash','-c',
                'cp /fixtures/key.pem /tmp/server.key && cp /fixtures/cert.pem /tmp/server.crt && chown postgres:postgres /tmp/server.key /tmp/server.crt && chmod 600 /tmp/server.key && exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/server.crt -c ssl_key_file=/tmp/server.key')
            for _ in range(60):
                if subprocess.run(['docker','exec',name,'pg_isready','-U','postgres'],capture_output=True).returncode==0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError('Test database did not become ready')
            run('docker','exec',name,'psql','-U','postgres','-d','partneragent','-v','ON_ERROR_STOP=1','-c',
                "CREATE ROLE agent_admin LOGIN CREATEROLE CREATEDB PASSWORD 'disposable-fixture-only'; ALTER DATABASE partneragent OWNER TO agent_admin; ALTER SCHEMA public OWNER TO agent_admin;")
            base=['docker','run','--rm','--platform','linux/amd64','--network',name,
                  '--env-file',str(root/'fixture.env'),'-v',str(root/'cert.pem')+':/app/rds-ca.pem:ro']
            run(*base,name)
            run(*base,name)  # Existing accounts must not have their passwords reset.
            run(*base,'-v',str(repo/'integration')+':/app/integration:ro','-e','PYTHONPATH=/app',
                name,'python','/app/integration/test_postgres.py')
        finally:
            subprocess.run(['docker','rm','-fv',name],capture_output=True)
            subprocess.run(['docker','network','rm',name],capture_output=True)
            subprocess.run(['docker','image','rm',name],capture_output=True)


if __name__=='__main__':
    main()
