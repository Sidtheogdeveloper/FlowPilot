from fastapi.testclient import TestClient
from sqlalchemy import select
from app.api import app
from app import models as db
from app import account_features as features
from test_runtime import client
from test_accounts import PASSWORD

def test_profile_and_password_rotation(client):
    assert client.patch('/auth/profile',json={'name':'Updated Analyst'}).status_code==200
    old=TestClient(app)
    assert old.post('/auth/login',json={'email':'test@example.org','password':PASSWORD}).status_code==200
    assert client.post('/auth/password',json={'current_password':'wrong','new_password':'A different testing password 456!'}).status_code==401
    response=client.post('/auth/password',json={'current_password':PASSWORD,'new_password':'A different testing password 456!'})
    assert response.status_code==200,response.text
    client.headers['X-FlowPilot-CSRF']=response.json()['csrf_token']
    assert old.get('/auth/me').status_code==401
    assert client.get('/auth/me').json()['user']['name']=='Updated Analyst'

def test_password_recovery_and_replay(client,monkeypatch):
    monkeypatch.setenv('SMTP_HOST','mail.invalid');sent=[]
    monkeypatch.setattr(features,'mail_link',lambda *args:sent.append(args))
    response=client.post('/auth/forgot-password',json={'email':'test@example.org'})
    assert response.status_code==200 and len(sent)==1
    raw=sent[0][2]
    with db.Session() as s:assert s.scalar(select(db.AccountToken)).token_hash!=raw
    result=client.post('/auth/reset-password',json={'token':raw,'password':'A recovered testing passphrase 567!'})
    assert result.status_code==200
    assert client.get('/auth/me').status_code==401
    assert client.post('/auth/reset-password',json={'token':raw,'password':PASSWORD}).status_code==400
    assert client.post('/auth/login',json={'email':'test@example.org','password':'A recovered testing passphrase 567!'}).status_code==200

def test_recovery_no_account_enumeration(client,monkeypatch):
    monkeypatch.setenv('SMTP_HOST','mail.invalid');sent=[]
    monkeypatch.setattr(features,'mail_link',lambda *args:sent.append(args))
    a=client.post('/auth/forgot-password',json={'email':'test@example.org'})
    b=client.post('/auth/forgot-password',json={'email':'absent@example.org'})
    assert a.json()==b.json() and len(sent)==1

def test_verification_and_token_expiry(client,monkeypatch):
    from datetime import timedelta
    monkeypatch.setenv('SMTP_HOST','mail.invalid');sent=[]
    monkeypatch.setattr(features,'mail_link',lambda *args:sent.append(args))
    assert client.post('/auth/send-verification').status_code==200
    assert client.post('/auth/verify-email',json={'token':sent[0][2]}).status_code==200
    assert client.get('/auth/me').json()['user']['email_verified']
    assert client.post('/auth/verify-email',json={'token':sent[0][2]}).status_code==400
    with db.Session() as s:user=s.scalar(select(db.User))
    raw=features.issue(user.id,'reset')
    with db.Session.begin() as s:
        row=s.scalar(select(db.AccountToken).where(db.AccountToken.purpose=='reset'));row.expires=db.now()-timedelta(seconds=1)
    assert client.post('/auth/reset-password',json={'token':raw,'password':PASSWORD}).status_code==400

def test_mail_configuration_is_explicit(client,monkeypatch):
    monkeypatch.delenv('SMTP_HOST',raising=False)
    assert not client.get('/auth/config').json()['password_reset_available']
    assert client.post('/auth/forgot-password',json={'email':'test@example.org'}).status_code==503
    assert client.post('/auth/send-verification').status_code==503

def test_production_configuration_and_verification_gate(client,monkeypatch):
    import pytest
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.delenv('APP_PUBLIC_URL',raising=False)
    with pytest.raises(RuntimeError):features.validate_deployment()
    monkeypatch.setenv('APP_PUBLIC_URL','https://flowpilot.example.org')
    monkeypatch.setenv('ALLOWED_ORIGINS','https://flowpilot.example.org')
    monkeypatch.setenv('SMTP_HOST','mail.invalid');monkeypatch.setenv('SMTP_FROM','hello@example.org')
    features.validate_deployment()
    assert client.get('/datasets').status_code==403
    assert client.get('/auth/me').status_code==200
    response=TestClient(app).post('/auth/login',json={'email':'test@example.org','password':PASSWORD})
    assert 'Secure' in response.headers['set-cookie'].split('; ')
