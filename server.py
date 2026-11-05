"""Тукан Батл: сервер + Telegram-бот (только стандартная библиотека, Python 3.8+).
Переменные: BOT_TOKEN (обязательно), WEBAPP_URL (https-адрес этого сервера),
CHANNEL (например @mychannel, бот должен быть админом канала), PORT, DB_PATH.
Запуск: python3 server.py. Отдаёт index.html, API и сам отвечает в боте."""
import hashlib,hmac,json,math,os,secrets,signal,sqlite3,sys,threading,time,urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import parse_qsl
D=os.path.dirname(os.path.abspath(__file__))
TOKEN=os.environ.get("BOT_TOKEN","");WEBAPP=os.environ.get("WEBAPP_URL","");CHANNEL=os.environ.get("CHANNEL","");BACKUP=os.environ.get("BACKUP_CHAT","");START=2000;DAY=86400;BOTNAME=[""]
CASES={c["id"]:c for c in json.load(open(os.path.join(D,"cases.json"),encoding="utf-8"))}
DBP=os.environ.get("DB_PATH",os.path.join(D,"game.db"))
def tg(method,**p):
    r=urllib.request.Request("https://api.telegram.org/bot%s/%s"%(TOKEN,method),json.dumps(p).encode(),{"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(r,timeout=40))["result"]
def upload(chat,path):
    bd=secrets.token_hex(8);f=open(path,"rb").read()
    body=("--%s\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n%s\r\n--%s\r\nContent-Disposition: form-data; name=\"document\"; filename=\"game.db\"\r\nContent-Type: application/octet-stream\r\n\r\n"%(bd,chat,bd)).encode()+f+("\r\n--%s--\r\n"%bd).encode()
    r=urllib.request.Request("https://api.telegram.org/bot%s/sendDocument"%TOKEN,body,{"Content-Type":"multipart/form-data; boundary="+bd})
    return json.load(urllib.request.urlopen(r,timeout=90))["result"]
def download(fid):
    fp=tg("getFile",file_id=fid)["file_path"]
    return urllib.request.urlopen("https://api.telegram.org/file/bot%s/%s"%(TOKEN,fp),timeout=90).read()
def restore():
    if not(TOKEN and BACKUP) or os.path.exists(DBP):return
    try:
        pm=tg("getChat",chat_id=BACKUP).get("pinned_message") or {};doc=pm.get("document")
        if doc:
            open(DBP,"wb").write(download(doc["file_id"]));print("база восстановлена из Telegram")
    except Exception as e:print("restore",e)
restore()
db=sqlite3.connect(DBP,check_same_thread=False,isolation_level=None)
db.executescript("""create table if not exists u(id integer primary key,balance integer,daily integer default 0,username text,first_name text,ref integer,sub integer default 0,refpaid integer default 0);
create table if not exists mines(user integer primary key,bet integer,n integer,traps text,safe text);
create table if not exists crash(user integer primary key,bet integer,cp real,t0 real);
create table if not exists inv(uid integer primary key autoincrement,user integer,case_id text,idx integer,v integer);
create table if not exists req(user integer,rid text,resp text,primary key(user,rid));
create table if not exists ledger(user integer,delta integer,reason text,ts integer);""")
L=threading.Lock()
def auth(init):
    if not TOKEN or not init:return None
    d=dict(parse_qsl(init,keep_blank_values=True));h=d.pop("hash","")
    chk="\n".join(k+"="+d[k] for k in sorted(d))
    key=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(key,chk.encode(),hashlib.sha256).hexdigest(),h):return None
    if time.time()-int(d.get("auth_date","0"))>86400:return None
    return json.loads(d["user"])
def pick(c):
    tot=sum(i["w"] for i in c["items"])*1000;r=secrets.randbelow(int(tot));s=0
    for k,i in enumerate(c["items"]):
        s+=i["w"]*1000
        if r<s:return k
    return 0
def bal(u):
    r=db.execute("select balance from u where id=?",(u,)).fetchone()
    if not r:db.execute("insert into u(id,balance) values(?,?)",(u,START));return START
    return r[0]
def add(u,d,why):db.execute("update u set balance=balance+? where id=?",(d,u));db.execute("insert into ledger values(?,?,?,?)",(u,d,why,int(time.time())))
def invlist(u):return [{"uid":a,"case":b,"i":c,"v":v} for a,b,c,v in db.execute("select uid,case_id,idx,v from inv where user=?",(u,))]
def touch(usr,ref=None):
    with L:
        i=usr["id"];new=not db.execute("select 1 from u where id=?",(i,)).fetchone()
        if new:db.execute("insert into u(id,balance,ref) values(?,?,?)",(i,START,ref if ref and ref!=i else None))
        db.execute("update u set username=?,first_name=? where id=?",(usr.get("username"),usr.get("first_name"),i))
def poll():
    off=0
    try:
        BOTNAME[0]=tg("getMe")["username"]
        if WEBAPP:tg("setChatMenuButton",menu_button={"type":"web_app","text":"Играть","web_app":{"url":WEBAPP}})
    except Exception as e:print("bot init",e)
    while True:
        try:
            for up in tg("getUpdates",offset=off,timeout=30):
                off=up["update_id"]+1;m=up.get("message") or {};cb=up.get("callback_query")
                if cb and cb.get("data")=="check":check(cb);continue
                t=m.get("text") or "";f=m.get("from")
                if f and t.startswith("/start"):
                    a=t.split(" ",1)[1] if " " in t else "";ref=int(a[4:]) if a.startswith("ref_") and a[4:].isdigit() else None
                    touch(f,ref)
                    welcome(m["chat"]["id"],f)
        except Exception as e:print("poll",e);time.sleep(5)
CLINK=os.environ.get("CHANNEL_LINK") or (("https://t.me/"+CHANNEL.lstrip("@")) if CHANNEL.startswith("@") else "")
MEMB={}
def is_member(u):
    if not CHANNEL:return True
    if MEMB.get(u,0)>time.time():return True
    try:st=tg("getChatMember",chat_id=CHANNEL,user_id=u)["status"]
    except Exception as e:print("member",e);return True
    ok=st in("member","administrator","creator")
    if ok:MEMB[u]=time.time()+600
    return ok
def play_kb():return {"inline_keyboard":[[{"text":"🦜 Играть","web_app":{"url":WEBAPP}}]]} if WEBAPP else None
def sub_kb():
    rows=[]
    if CLINK:rows.append([{"text":"📢 Подписаться","url":CLINK}])
    rows.append([{"text":"✅ Проверить","callback_data":"check"}]);return {"inline_keyboard":rows}
def welcome(chat,f):
    name=f.get("first_name") or "друг"
    if is_member(f["id"]):tg("sendMessage",chat_id=chat,text="Привет, %s! 🦜 Добро пожаловать в Тукан Батл. Жми «Играть»."%name,reply_markup=play_kb())
    else:tg("sendMessage",chat_id=chat,text="Привет, %s! 🦜 Добро пожаловать в Тукан Батл!\n\nЧтобы пользоваться ботом, подпишитесь на наш канал, а затем нажмите «Проверить»."%name,reply_markup=sub_kb())
def check(cb):
    f=cb["from"];touch(f)
    if not is_member(f["id"]):
        tg("answerCallbackQuery",callback_query_id=cb["id"],text="Вы ещё не подписаны на канал. Подпишитесь и нажмите «Проверить» снова.",show_alert=True);return
    msg="Отлично, подписка подтверждена! Жми «Играть»."
    with L:
        db.execute("begin immediate")
        try:
            r=claim_sub(f["id"]);db.execute("commit")
            if r.get("claimed"):msg="Отлично, подписка подтверждена! Вам начислено 1000 🪶. Жми «Играть»."
        except BaseException:db.execute("rollback")
    tg("answerCallbackQuery",callback_query_id=cb["id"])
    tg("sendMessage",chat_id=cb["message"]["chat"]["id"],text=msg,reply_markup=play_kb())
LASTB=[0]
def backup(force=False):
    if not(TOKEN and BACKUP):return
    with L:
        if not force and db.total_changes==LASTB[0]:return
        tmp=DBP+".bak";dst=sqlite3.connect(tmp);db.backup(dst);dst.close();LASTB[0]=db.total_changes
    try:
        m=upload(BACKUP,tmp);tg("pinChatMessage",chat_id=BACKUP,message_id=m["message_id"],disable_notification=True)
    except Exception as e:print("backup",e);LASTB[0]=0
def backup_loop():
    while True:time.sleep(300);backup()
def claim_sub(u):
    if not CHANNEL:raise Err(503,"no_channel")
    r=db.execute("select sub,ref,refpaid from u where id=?",(u,)).fetchone()
    if r[0]:return {"claimed":False,"balance":bal(u)}
    try:st=tg("getChatMember",chat_id=CHANNEL,user_id=u)["status"]
    except Exception:raise Err(502,"tg_error")
    if st not in("member","administrator","creator"):return {"claimed":False,"balance":bal(u)}
    db.execute("update u set sub=1 where id=?",(u,));add(u,1000,"sub")
    if r[1] and not r[2]:
        db.execute("update u set refpaid=1 where id=?",(u,))
        if db.execute("select 1 from u where id=?",(r[1],)).fetchone():add(r[1],1000,"referral:%d"%u)
    return {"claimed":True,"balance":bal(u)}
PM=[15,6,2.2,1.1,.5,.3,.5,1.1,2.2,6,15]
def emult(k,m):
    x=0.92
    for i in range(k):x*=(25-i)/(25-m-i)
    return x
ITEMS={}
for _c in CASES.values():
    if _c["cost"]:
        for _k,_i in enumerate(_c["items"]):ITEMS.setdefault(_i["n"],(_c["id"],_k,_i["v"]))
class Err(Exception):
    def __init__(s,code,msg):s.code=code;s.msg=msg
def act(u,path,b):
    rid=str(b.get("request_id",""))[:64]
    if path=="/api/me":return {"balance":bal(u),"inventory":invlist(u),"referral_link":("https://t.me/%s?start=ref_%d"%(BOTNAME[0],u)) if BOTNAME[0] else None}
    if path=="/api/referral-link":
        if not BOTNAME[0]:raise Err(503,"no_bot")
        return {"referral_link":"https://t.me/%s?start=ref_%d"%(BOTNAME[0],u)}
    if path=="/api/claim-subscription":bal(u);return claim_sub(u)
    if path=="/api/leaderboard":
        top=db.execute("select id,username,first_name,balance from u order by balance desc limit 20").fetchall();b=bal(u)
        return {"players":[{"username":x[1],"first_name":x[2],"balance":x[3],"is_me":x[0]==u} for x in top],"me":{"rank":db.execute("select count(*)+1 from u where balance>?",(b,)).fetchone()[0],"balance":b}}
    if path=="/api/crash/state":
        r=db.execute("select cp,t0 from crash where user=?",(u,)).fetchone()
        if not r:return {"none":True}
        if math.exp(.22*max(0,time.time()-r[1]-.25))>=r[0]:db.execute("delete from crash where user=?",(u,));return {"crashed":True,"cp":r[0]}
        return {"crashed":False}
    if not rid:raise Err(400,"bad_request")
    old=db.execute("select resp from req where user=? and rid=?",(u,rid)).fetchone()
    if old:return json.loads(old[0])
    bal(u);out=None
    if path=="/api/case/open":
        c=CASES.get(b.get("case_id"));n=int(b.get("count",1))
        if not c or not 1<=n<=10:raise Err(400,"bad_request")
        if c["cost"]==0:
            n=1
            if time.time()-db.execute("select daily from u where id=?",(u,)).fetchone()[0]<DAY:raise Err(409,"daily_used")
            db.execute("update u set daily=? where id=?",(int(time.time()),u))
        if bal(u)<c["cost"]*n:raise Err(402,"insufficient_funds")
        add(u,-c["cost"]*n,"open:"+c["id"]);got=[]
        for _ in range(n):
            k=pick(c);v=c["items"][k]["v"]
            cur=db.execute("insert into inv(user,case_id,idx,v) values(?,?,?,?)",(u,c["id"],k,v))
            got.append({"uid":cur.lastrowid,"i":k,"v":v})
        out={"items":got}
    elif path=="/api/inventory/sell":
        ids=[r[0] for r in db.execute("select uid from inv where user=?",(u,))] if b.get("all") else [int(x) for x in b.get("uids",[])]
        tot=0
        for i in ids:
            r=db.execute("select v from inv where uid=? and user=?",(i,u)).fetchone()
            if r:tot+=r[0];db.execute("delete from inv where uid=?",(i,))
        add(u,tot,"sell");out={"sold":tot}
    elif path=="/api/crash/start":
        bet=int(b.get("bet",0))
        if not 50<=bet<=100000:raise Err(400,"bad_request")
        if bal(u)<bet:raise Err(402,"insufficient_funds")
        cp=min(100,max(1,0.9/(1-secrets.randbelow(10**9)/10**9)));cp=math.floor(cp*100)/100
        add(u,-bet,"crash");db.execute("insert or replace into crash values(?,?,?,?)",(u,bet,cp,time.time()));out={}
    elif path=="/api/crash/cashout":
        r=db.execute("select bet,cp,t0 from crash where user=?",(u,)).fetchone()
        if not r:raise Err(409,"no_round")
        bet,cp,t0=r;claim=float(b.get("claim",0));ms=math.exp(.22*(time.time()-t0))
        db.execute("delete from crash where user=?",(u,))
        if claim<1 or claim>=cp or math.exp(.22*max(0,time.time()-t0-.25))>=cp:out={"crashed":True,"cp":cp}
        else:
            m=min(claim,ms);win=math.floor(bet*m);add(u,win,"crash_win");out={"crashed":False,"m":m,"win":win,"cp":cp}
    elif path=="/api/upgrade":
        f=int(b.get("feathers",0));uids=[int(x) for x in b.get("uids",[])];tn=b.get("target")
        if not(0<=f<=100000 and len(set(uids))==len(uids)<=6 and tn in ITEMS):raise Err(400,"bad_request")
        its=[db.execute("select v from inv where uid=? and user=?",(x,u)).fetchone() for x in uids]
        if any(i is None for i in its):raise Err(409,"no_item")
        t=f+sum(i[0] for i in its);tv=ITEMS[tn][2]
        if t<25 or not tv>t*1.05 or tv>t*80:raise Err(400,"bad_request")
        if bal(u)<f:raise Err(402,"insufficient_funds")
        ch=min(75,t/tv*92);win=secrets.randbelow(10**6)/10**4<ch
        add(u,-f,"upgrade")
        for x in uids:db.execute("delete from inv where uid=?",(x,))
        nid=None
        if win:cid,k,v=ITEMS[tn];nid=db.execute("insert into inv(user,case_id,idx,v) values(?,?,?,?)",(u,cid,k,v)).lastrowid
        out={"win":win,"chance":ch,"uid":nid}
    elif path=="/api/battle/play":
        c=CASES.get(b.get("case_id"))
        if not c or c["cost"]<=0:raise Err(400,"bad_request")
        if bal(u)<c["cost"]:raise Err(402,"insufficient_funds")
        add(u,-c["cost"],"battle");ia,ib=pick(c),pick(c);va,vb=c["items"][ia]["v"],c["items"][ib]["v"];uids=[None,None]
        put=lambda k,v:db.execute("insert into inv(user,case_id,idx,v) values(?,?,?,?)",(u,c["id"],k,v)).lastrowid
        if va>vb:uids=[put(ia,va),put(ib,vb)]
        elif va==vb:uids=[put(ia,va),None]
        out={"a":ia,"b":ib,"uids":uids}
    elif path=="/api/plinko/drop":
        bet=int(b.get("bet",0))
        if not 25<=bet<=100000:raise Err(400,"bad_request")
        if bal(u)<bet:raise Err(402,"insufficient_funds")
        ks=[0]
        for _ in range(10):ks.append(ks[-1]+secrets.randbelow(2))
        win=math.floor(bet*PM[ks[-1]]);add(u,-bet,"plinko");add(u,win,"plinko_win");out={"ks":ks,"win":win}
    elif path=="/api/mines/start":
        bet=int(b.get("bet",0));n=int(b.get("mines",0))
        if not(50<=bet<=100000 and 2<=n<=24):raise Err(400,"bad_request")
        if bal(u)<bet:raise Err(402,"insufficient_funds")
        add(u,-bet,"mines");t=set()
        while len(t)<n:t.add(secrets.randbelow(25))
        db.execute("insert or replace into mines values(?,?,?,?,?)",(u,bet,n,json.dumps(sorted(t)),"[]"));out={}
    elif path in("/api/mines/open","/api/mines/cashout"):
        r=db.execute("select bet,n,traps,safe from mines where user=?",(u,)).fetchone()
        if not r:raise Err(409,"no_round")
        bet,n=r[0],r[1];t=json.loads(r[2]);sf=json.loads(r[3])
        if path.endswith("open"):
            c=int(b.get("cell",-1))
            if not 0<=c<25:raise Err(400,"bad_request")
            if c in t:db.execute("delete from mines where user=?",(u,));out={"safe":False,"traps":t}
            else:
                if c not in sf:sf.append(c)
                if len(sf)==25-n:
                    win=math.floor(bet*emult(len(sf),n));add(u,win,"mines_win");db.execute("delete from mines where user=?",(u,));out={"safe":True,"done":True,"win":win,"traps":t}
                else:db.execute("update mines set safe=? where user=?",(json.dumps(sf),u));out={"safe":True,"count":len(sf)}
        else:
            if not sf:raise Err(409,"no_safe_cell")
            win=math.floor(bet*emult(len(sf),n));add(u,win,"mines_win");db.execute("delete from mines where user=?",(u,));out={"win":win,"traps":t}
    else:raise Err(404,"not_found")
    out["balance"]=bal(u);db.execute("insert into req values(?,?,?)",(u,rid,json.dumps(out)));return out
class H(BaseHTTPRequestHandler):
    def send(s,code,obj=None,raw=None,ct="application/json"):
        data=raw if raw is not None else json.dumps(obj,ensure_ascii=False).encode()
        s.send_response(code);s.send_header("Content-Type",ct+"; charset=utf-8");s.send_header("Content-Length",str(len(data)));s.end_headers();s.wfile.write(data)
    def do_GET(s):
        if s.path.startswith("/api/"):return s.api({})
        p=os.path.join(D,"index.html")
        if not os.path.exists(p):p=os.path.join(D,"..","index.html")
        s.send(200,raw=open(p,"rb").read(),ct="text/html")
    def do_POST(s):
        try:b=json.loads(s.rfile.read(int(s.headers.get("Content-Length",0))or 2)or b"{}")
        except Exception:return s.send(400,{"error":"bad_request"})
        s.api(b)
    def api(s,b):
        usr=auth(s.headers.get("X-Telegram-Init-Data",""))
        if not usr:return s.send(401,{"error":"bad_init_data"})
        u=usr["id"];touch(usr)
        if not is_member(u):return s.send(403,{"error":"not_subscribed","channel":CLINK})
        try:
            with L:
                db.execute("begin immediate")
                try:r=act(u,s.path.split("?")[0],b);db.execute("commit")
                except BaseException:db.execute("rollback");raise
            s.send(200,r)
        except Err as e:s.send(e.code,{"error":e.msg})
        except Exception:s.send(500,{"error":"server"})
    def log_message(s,*a):pass
def bye(*a):backup(True);sys.exit(0)
if __name__=="__main__":
    if TOKEN:threading.Thread(target=poll,daemon=True).start();threading.Thread(target=backup_loop,daemon=True).start()
    signal.signal(signal.SIGTERM,bye)
    ThreadingHTTPServer(("0.0.0.0",int(os.environ.get("PORT",8080))),H).serve_forever()
