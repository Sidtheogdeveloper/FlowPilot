"""Authenticate live acceptance scripts without embedding or printing credentials."""
import os,getpass

def sign_in(client):
    address=os.getenv('FLOWPILOT_TEST_EMAIL') or input('FlowPilot test account email: ')
    password=os.getenv('FLOWPILOT_TEST_PASSWORD') or getpass.getpass('FlowPilot test account password: ')
    response=client.post('/auth/login',json={'email':address,'password':password})
    response.raise_for_status()
    client.headers['X-FlowPilot-CSRF']=response.json()['csrf_token']
