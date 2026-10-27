"""Profile, password recovery, email verification and account session controls."""
import os,secrets,uuid,smtplib,ssl
from email.message import EmailMessage
from datetime import timedelta
from urllib.parse import urlencode,urlparse
from fastapi import APIRouter,Request,Response,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select,update
from . import models as db
from . import auth

router=APIRouter(prefix='/auth',tags=['Accounts'])

def validate_deployment():
    if os.getenv('APP_ENV')!='production':return
    base=os.getenv('APP_PUBLIC_URL','')
    if urlparse(base).scheme!='https' or not urlparse(base).hostname:raise RuntimeError('Production requires an HTTPS APP_PUBLIC_URL')
    if not os.getenv('ALLOWED_ORIGINS') or base.rstrip('/') not in os.environ['ALLOWED_ORIGINS'].split(','):raise RuntimeError('ALLOWED_ORIGINS must contain APP_PUBLIC_URL')
    if not os.getenv('SMTP_HOST') or not os.getenv('SMTP_FROM'):raise RuntimeError('Production registration requires SMTP_HOST and SMTP_FROM for verification and recovery')

def send_mail(address,subject,body):
    host=os.getenv('SMTP_HOST')
    if not host:raise HTTPException(503,'Email delivery is not configured. Contact the workspace operator.')
    message=EmailMessage();message['From']=os.getenv('SMTP_FROM','flowpilot@localhost');message['To']=address;message['Subject']=subject;message.set_content(body)
    port=int(os.getenv('SMTP_PORT','587'))
    if os.getenv('SMTP_SSL','false').lower()=='true':
        transport=smtplib.SMTP_SSL(host,port,timeout=15,context=ssl.create_default_context())
    else:transport=smtplib.SMTP(host,port,timeout=15)
    with transport as smtp:
        if os.getenv('SMTP_SSL','false').lower()!='true':
            if os.getenv('SMTP_STARTTLS','true').lower()=='true':smtp.starttls(context=ssl.create_default_context())
            elif os.getenv('APP_ENV')=='production':raise RuntimeError('Production SMTP requires TLS')
        if os.getenv('SMTP_USERNAME'):smtp.login(os.environ['SMTP_USERNAME'],os.environ.get('SMTP_PASSWORD',''))
        smtp.send_message(message)

def issue(user_id,purpose):
    raw=secrets.token_urlsafe(40)
    with db.Session.begin() as s:
        # Serialize issuance per user so concurrent requests cannot create multiple valid tokens.
        s.get(db.User,user_id,with_for_update=True)
        s.execute(update(db.AccountToken).where(db.AccountToken.user_id==user_id,db.AccountToken.purpose==purpose).values(consumed=True))
        s.add(db.AccountToken(id=str(uuid.uuid4()),user_id=user_id,purpose=purpose,token_hash=auth.digest(raw),expires=db.now()+timedelta(minutes=30)))
    return raw

def mail_link(address,purpose,raw):
    base=os.getenv('APP_PUBLIC_URL','http://localhost:18080').rstrip('/')
    link=base+'/#'+urlencode({'action':purpose,'token':raw})
    send_mail(address,'FlowPilot: '+('reset your password' if purpose=='reset' else 'verify your email'),f'Open this link to {"reset your password" if purpose=="reset" else "verify your email"}:\n\n{link}\n\nThis link expires in 30 minutes and can be used once. If you did not request it, ignore this email.')

def token_user(s,raw,purpose):
    if not 20<=len(raw)<=160:raise HTTPException(400,'This link is invalid or expired')
    row=s.scalar(select(db.AccountToken).where(db.AccountToken.token_hash==auth.digest(raw),db.AccountToken.purpose==purpose))
    if not row:raise HTTPException(400,'This link is invalid or expired')
    user=s.get(db.User,row.user_id,with_for_update=True)
    row=s.get(db.AccountToken,row.id,with_for_update=True,populate_existing=True)
    if not row or row.consumed or row.expires<=db.now():raise HTTPException(400,'This link is invalid or expired')
    if not user or not user.active:raise HTTPException(400,'This link is invalid or expired')
    row.consumed=True
    return user

class NameChange(BaseModel):name:str=Field(min_length=1,max_length=80)
class PasswordChange(BaseModel):current_password:str=Field(max_length=128);new_password:str=Field(max_length=128)
class Address(BaseModel):email:str=Field(max_length=254)
class Token(BaseModel):token:str=Field(max_length=160)
class Reset(Token):password:str=Field(max_length=128)

@router.patch('/profile')
def profile(payload:NameChange,request:Request):
    user=auth.require_user(request);name=payload.name.strip()
    if not name or any(ord(c)<32 for c in name):raise HTTPException(422,'Enter a valid name')
    with db.Session.begin() as s:
        row=s.get(db.User,user.id);row.name=name;s.flush();return auth.profile(row)

@router.post('/password')
def change_password(payload:PasswordChange,request:Request,response:Response):
    user=auth.require_user(request);auth.limit(request,'password-change',user.id,10);auth.password(payload.new_password)
    with db.Session.begin() as s:
        row=s.get(db.User,user.id,with_for_update=True)
        try:auth.hasher.verify(row.password_hash,payload.current_password)
        except (auth.VerificationError,auth.InvalidHashError):raise HTTPException(401,'Current password is incorrect')
        row.password_hash=auth.hasher.hash(payload.new_password);auth.revoke_all(s,row.id)
        s.execute(update(db.AccountToken).where(db.AccountToken.user_id==row.id).values(consumed=True))
        return auth.new_session(s,row,request,response)

@router.post('/forgot-password')
def forgot(payload:Address,request:Request):
    address=auth.email(payload.email);auth.limit(request,'recovery-ip',maximum=20);auth.limit(request,'recovery',address,3)
    if not os.getenv('SMTP_HOST'):raise HTTPException(503,'Password recovery needs an email service. Contact the workspace operator.')
    with db.Session() as s:user=s.scalar(select(db.User).where(db.User.email==address,db.User.active==True))
    if user:
        raw=issue(user.id,'reset')
        try:mail_link(address,'reset',raw)
        except Exception:
            import logging;logging.warning('Account recovery email delivery failed')
    return {'message':'If an active account matches, a recovery email will be sent. Check your inbox or contact the workspace operator.'}

@router.post('/reset-password')
def reset(payload:Reset,request:Request):
    auth.limit(request,'reset',maximum=20);auth.password(payload.password)
    with db.Session.begin() as s:
        user=token_user(s,payload.token,'reset');user.password_hash=auth.hasher.hash(payload.password);auth.revoke_all(s,user.id)
        s.execute(update(db.AccountToken).where(db.AccountToken.user_id==user.id).values(consumed=True))
    return {'message':'Password updated. Sign in with your new password.'}

@router.post('/send-verification')
def send_verification(request:Request):
    user=auth.require_user(request);auth.limit(request,'verification',user.id,3)
    if user.email_verified:return {'message':'Your email is already verified.'}
    if not os.getenv('SMTP_HOST'):raise HTTPException(503,'Email verification is not configured. Contact the workspace operator.')
    raw=issue(user.id,'verify')
    try:mail_link(user.email,'verify',raw)
    except Exception:raise HTTPException(503,'Verification email could not be delivered. Please try again later.')
    return {'message':'Verification email sent. The link expires in 30 minutes.'}

@router.post('/verify-email')
def verify(payload:Token,request:Request):
    auth.limit(request,'verify',maximum=20)
    with db.Session.begin() as s:token_user(s,payload.token,'verify').email_verified=True
    return {'message':'Email verified. You can now use your workspace.'}
