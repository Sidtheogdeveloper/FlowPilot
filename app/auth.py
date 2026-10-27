"""Database-backed browser sessions, password authentication and CSRF protection."""
import hashlib,hmac,os,re,secrets,uuid
from datetime import timedelta
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError,InvalidHashError
from fastapi import APIRouter,HTTPException,Request,Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select,update
from sqlalchemy.dialects.postgresql import insert
from starlette.middleware.base import BaseHTTPMiddleware
from . import models as db
from .tenancy import owner

router=APIRouter(prefix='/auth',tags=['Accounts'])
hasher=PasswordHasher(time_cost=3,memory_cost=19456,parallelism=1)
dummy_hash=hasher.hash(secrets.token_urlsafe(24))
COOKIE='flowpilot_session'
IDLE=timedelta(hours=1)
LIFETIME=timedelta(hours=24)
def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def csrf(value):return hmac.new(value.encode(),b'flowpilot-csrf-v1',hashlib.sha256).hexdigest()
def secure_cookie():return os.getenv('APP_ENV','development')=='production'
def registration_open():return os.getenv('ALLOW_REGISTRATION','true').lower()=='true'
def email(value):
    value=value.strip().lower()
    if len(value)>254 or not re.fullmatch(r'[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}',value):
        raise HTTPException(422,'Enter a valid email address')
    return value
def password(value):
    if not 15<=len(value)<=128:raise HTTPException(422,'Use a password with 15 to 128 characters')
    if value.lower() in {'passwordpassword','password123456789','123456789012345','qwertyqwertyqwerty'}:
        raise HTTPException(422,'Choose a less common password or a longer passphrase')
    return value
def profile(user):return {'id':user.id,'email':user.email,'name':user.name,'email_verified':user.email_verified,'created':user.created.isoformat()}
def require_user(request):
    user=getattr(request.state,'user',None)
    if not user:raise HTTPException(401,'Please sign in to continue')
    return user
def check_origin(request):
    origin=request.headers.get('origin')
    if request.headers.get('sec-fetch-site')=='cross-site':return False
    if not origin:return True
    allowed={str(request.base_url).rstrip('/')}
    allowed.update(x.strip().rstrip('/') for x in os.getenv('ALLOWED_ORIGINS','http://localhost:18080,http://127.0.0.1:18080,http://localhost:8080,http://127.0.0.1:8080,http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173').split(',') if x.strip())
    return origin.rstrip('/') in allowed
def limit(request,action,identity='',maximum=10):
    # Use the direct peer address; forwarded client addresses are not trusted here.
    peer=request.client.host if request.client else 'unknown'
    key=digest(action+'|'+peer+'|'+identity)
    now=db.now();blocked=False
    with db.Session.begin() as s:
        s.execute(insert(db.AuthThrottle).values(id=key,count=0,window=now).on_conflict_do_nothing())
        row=s.get(db.AuthThrottle,key,with_for_update=True)
        if row.window<now-timedelta(minutes=15):row.window=now;row.count=0
        blocked=row.count>=maximum
        if not blocked:row.count+=1
    if blocked:raise HTTPException(429,'Too many attempts. Please try again later.',headers={'Retry-After':'900'})
def revoke_all(s,user_id):s.execute(update(db.UserSession).where(db.UserSession.user_id==user_id).values(revoked=True))
def new_session(s,user,request,response):
    raw=secrets.token_urlsafe(48);now=db.now()
    sessions=list(s.scalars(select(db.UserSession).where(db.UserSession.user_id==user.id,db.UserSession.revoked==False,db.UserSession.expires>now).order_by(db.UserSession.created)))
    for old in sessions[:-4] if len(sessions)>=5 else []:old.revoked=True
    row=db.UserSession(id=str(uuid.uuid4()),user_id=user.id,token_hash=digest(raw),created=now,last_seen=now,expires=now+LIFETIME,agent=request.headers.get('user-agent','')[:180])
    s.add(row)
    response.set_cookie(COOKIE,raw,max_age=int(LIFETIME.total_seconds()),httponly=True,secure=secure_cookie(),samesite='lax',path='/')
    return {'user':profile(user),'csrf_token':csrf(raw),'session_id':row.id}

class Credentials(BaseModel):
    email:str
    password:str
class Registration(Credentials):name:str

@router.get('/config')
def config():return {'brand':'FlowPilot','registration_open':registration_open(),'password_min_length':15,'password_max_length':128,'password_reset_available':bool(os.getenv('SMTP_HOST'))}
@router.post('/register',status_code=201)
def register(payload:Registration,request:Request,response:Response):
    if not registration_open():raise HTTPException(403,'New account registration is currently closed')
    address=email(payload.email);password(payload.password)
    name=payload.name.strip()
    if not 1<=len(name)<=80 or any(ord(c)<32 for c in name):raise HTTPException(422,'Enter a name between 1 and 80 characters')
    limit(request,'register',maximum=5)
    hashed=hasher.hash(payload.password)
    from sqlalchemy.exc import IntegrityError
    try:
        with db.Session.begin() as s:
            user=db.User(id=str(uuid.uuid4()),email=address,name=name,password_hash=hashed)
            s.add(user);s.flush();result=new_session(s,user,request,response)
    except IntegrityError:raise HTTPException(409,'An account cannot be created with those details. Try signing in or recovering your account.')
    return result
@router.post('/login')
def login(payload:Credentials,request:Request,response:Response):
    address=email(payload.email)
    if len(payload.password)>128:raise HTTPException(401,'Email or password is incorrect')
    limit(request,'login-ip',maximum=50);limit(request,'login',address,10)
    with db.Session.begin() as s:
        user=s.scalar(select(db.User).where(db.User.email==address))
        try:valid=hasher.verify(user.password_hash if user else dummy_hash,payload.password)
        except (VerificationError,InvalidHashError):valid=False
        if not valid or not user or not user.active:raise HTTPException(401,'Email or password is incorrect')
        if hasher.check_needs_rehash(user.password_hash):user.password_hash=hasher.hash(payload.password)
        # Rotate an existing session when re-authenticating.
        if getattr(request.state,'session_id',None):s.get(db.UserSession,request.state.session_id).revoked=True
        return new_session(s,user,request,response)
@router.get('/me')
def me(request:Request):
    user=require_user(request)
    return {'user':profile(user),'csrf_token':csrf(request.cookies[COOKIE]),'session_id':request.state.session_id}
@router.post('/logout')
def logout(request:Request,response:Response):
    require_user(request)
    with db.Session.begin() as s:s.get(db.UserSession,request.state.session_id).revoked=True
    response.delete_cookie(COOKIE,path='/',secure=secure_cookie(),httponly=True,samesite='lax')
    return {'ok':True}
@router.post('/logout-all')
def logout_all(request:Request,response:Response):
    user=require_user(request)
    with db.Session.begin() as s:revoke_all(s,user.id)
    response.delete_cookie(COOKIE,path='/',secure=secure_cookie(),httponly=True,samesite='lax')
    return {'ok':True}
@router.get('/sessions')
def sessions(request:Request):
    user=require_user(request);now=db.now()
    with db.Session() as s:
        return [{'id':x.id,'current':x.id==request.state.session_id,'created':x.created.isoformat(),'last_seen':x.last_seen.isoformat(),'agent':x.agent} for x in s.scalars(select(db.UserSession).where(db.UserSession.user_id==user.id,db.UserSession.revoked==False,db.UserSession.expires>now,db.UserSession.last_seen>now-IDLE).order_by(db.UserSession.created.desc()))]
@router.delete('/sessions/{identity}')
def end_session(identity:str,request:Request):
    user=require_user(request)
    with db.Session.begin() as s:
        row=s.get(db.UserSession,identity)
        if not row or row.user_id!=user.id:raise HTTPException(404,'Session not found')
        row.revoked=True
    return {'ok':True}

class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        if request.method=='OPTIONS':return await call_next(request)
        unsafe=request.method not in ('GET','HEAD','OPTIONS')
        if unsafe and not check_origin(request):return JSONResponse({'detail':'This request origin is not allowed'},status_code=403)
        raw=request.cookies.get(COOKIE,'');principal=None;session=None;now=db.now()
        if raw and len(raw)<200:
            with db.Session.begin() as s:
                session=s.scalar(select(db.UserSession).where(db.UserSession.token_hash==digest(raw)))
                if session and not session.revoked and session.expires>now and session.last_seen>now-IDLE:
                    candidate=s.get(db.User,session.user_id)
                    if candidate and candidate.active:
                        principal=candidate
                        if session.last_seen<now-timedelta(minutes=1):session.last_seen=now
        path=request.url.path.rstrip('/') or '/'
        public=path in ('/health','/auth/config','/auth/register','/auth/login','/auth/forgot-password','/auth/reset-password','/auth/verify-email')
        if not principal and not public:return JSONResponse({'detail':'Please sign in to continue'},status_code=401)
        if principal and unsafe and path not in ('/auth/login','/auth/register','/auth/forgot-password','/auth/reset-password','/auth/verify-email'):
            if not hmac.compare_digest(request.headers.get('x-flowpilot-csrf',''),csrf(raw)):
                return JSONResponse({'detail':'Your session could not validate this request. Refresh and try again.'},status_code=403)
        request.state.user=principal;request.state.session_id=session.id if principal else None
        token=owner.set(principal.id if principal else None)
        try:response=await call_next(request)
        finally:owner.reset(token)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Cache-Control']='no-store'
        if response.headers.get('content-type','').startswith('text/html'):
            response.headers['Content-Security-Policy']="sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        return response
