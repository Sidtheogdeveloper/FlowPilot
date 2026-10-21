import os,time,uuid,threading,logging
from sqlalchemy.exc import DBAPIError
from . import models as db
from .catalogue import CATALOGUE
from .coordinator import register_worker,heartbeat,claim,finish
from .handlers import execute

def run(stop=None,id=None,cpu=None,memory_mb=None,speed=None):
    stop=stop or threading.Event();id=id or os.getenv('WORKER_ID','worker-'+str(uuid.uuid4())[:8]);generation=str(uuid.uuid4())
    register_worker(id,generation,int(cpu or os.getenv('WORKER_CPU',2)),int(memory_mb or os.getenv('WORKER_MEMORY_MB',512)),list(CATALOGUE),float(speed or os.getenv('WORKER_SPEED',1)))
    def beats():
        while not stop.is_set():
            try:
                if not heartbeat(id,generation):stop.set();return
            except DBAPIError:logging.warning('Heartbeat temporarily cannot reach database; will retry')
            stop.wait(2)
    thread=threading.Thread(target=beats,daemon=True);thread.start()
    while not stop.is_set():
        try:job=claim(id,generation)
        except DBAPIError:
            logging.warning('Worker temporarily cannot claim database work; will retry');stop.wait(.5);continue
        if job:
            try:output=execute(job['handler'],job['inputs'],job['parameters']);result={'artifact':output['id']}
            except Exception as exc:result={'error':str(exc)}
            for attempt in range(6):
                try:finish(job['attempt'],generation,**result);break
                except DBAPIError:
                    logging.warning('Completion temporarily cannot reach database; retrying same fenced result');stop.wait(.5)
                except ValueError as exc:
                    finish(job['attempt'],generation,error='Result contract rejected: '+str(exc));break
        else:stop.wait(.05)
    thread.join(3)
if __name__=='__main__':db.init_db();run()
