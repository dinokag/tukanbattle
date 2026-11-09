"""Тукан Батл: сервер + Telegram-бот (только стандартная библиотека, Python 3.8+).
Переменные: BOT_TOKEN (обязательно), WEBAPP_URL (https-адрес этого сервера),
CHANNEL (например @mychannel, бот должен быть админом канала), PORT, DB_PATH.
Запуск: python3 server.py. Отдаёт index.html, API и сам отвечает в боте."""
import hashlib,hmac,json,math,os,secrets,signal,sqlite3,sys,threading,time,urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import parse_qsl
D=os.path.dirname(os.path.abspath(__file__))
TOKEN=os.environ.get("BOT_TOKEN","");WEBAPP=os.environ.get("WEBAPP_URL","");CHANNEL=os.environ.get("CHANNEL","");BACKUP=os.environ.get("BACKUP_CHAT","");START=0;DAY=86400;BOTNAME=[""]
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
create table if not exists promo(code text primary key,feathers integer,max_uses integer,used integer default 0,expires integer default 0);
create table if not exists promo_used(code text,user integer,primary key(code,user));
create table if not exists drops(id integer primary key autoincrement,ts integer,who text,case_id text,idx integer,v integer);
create table if not exists pay(charge text primary key,user integer,kind text,stars integer,ts integer,refunded integer default 0);
create table if not exists mines(user integer primary key,bet integer,n integer,traps text,safe text);
create table if not exists crash(user integer primary key,bet integer,cp real,t0 real);
create table if not exists inv(uid integer primary key autoincrement,user integer,case_id text,idx integer,v integer);
create table if not exists req(user integer,rid text,resp text,primary key(user,rid));
create table if not exists ledger(user integer,delta integer,reason text,ts integer);""")
def mig():
    cols={r[1] for r in db.execute("pragma table_info(u)")}
    for c,d in (("banned","integer default 0"),("clicks","integer default 0"),("ckday","text"),("ckcnt","integer default 0"),("mult","integer default 1"),("nick","integer default 0")):
        if c not in cols:db.execute("alter table u add column %s %s"%(c,d))
mig()
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
                pq=up.get("pre_checkout_query")
                if pq:pre_checkout(pq);continue
                if cb and cb.get("data")=="check":check(cb);continue
                if cb and str(cb.get("data","")).startswith("adm_") and cb["from"]["id"] in ADMINS:
                    ch=cb["message"]["chat"]["id"]
                    try:say(ch,admin({"adm_top":"/top","adm_pay":"/payments","adm_promo":"/promolist"}.get(cb["data"],"/stats"),ch))
                    except Exception as e:say(ch,getattr(e,"msg",str(e)))
                    tg("answerCallbackQuery",callback_query_id=cb["id"]);continue
                t=m.get("text") or "";f=m.get("from")
                if f and m.get("successful_payment"):paid(f,m["successful_payment"]);continue
                if f and t.startswith("/") and not t.startswith("/start"):handle_cmd(f,m["chat"]["id"],t);continue
                if f and t.startswith("/start"):
                    a=t.split(" ",1)[1] if " " in t else "";ref=int(a[4:]) if a.startswith("ref_") and a[4:].isdigit() else None
                    touch(f,ref)
                    if tx(lambda:banned(f["id"])):say(m["chat"]["id"],"⛔ Доступ ограничен.");continue
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
    if ok:MEMB[u]=time.time()+60
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
def parse_payload(pl):
    try:
        p=pl.split("|")
        return (int(p[1]),p[2],int(p[3])) if len(p)==5 and p[0]=="p1" else None
    except Exception:return None
def price_ok(kind,stars):
    return MIN_STARS<=stars<=MAX_STARS if kind=="feathers" else (kind in BOOST and BOOST[kind][1]==stars)
def pre_checkout(pq):
    ok=False
    try:
        x=parse_payload(pq.get("invoice_payload",""))
        if x and x[0]==pq["from"]["id"] and pq.get("currency")=="XTR" and pq.get("total_amount")==x[2] and price_ok(x[1],x[2]):
            def chk():
                if banned(x[0]):return False
                return not(x[1] in BOOST and (db.execute("select mult from u where id=?",(x[0],)).fetchone() or [1])[0]>=BOOST[x[1]][0])
            ok=tx(chk)
    except Exception as e:print("precheckout",e)
    try:tg("answerPreCheckoutQuery",pre_checkout_query_id=pq["id"],ok=bool(ok),**({} if ok else {"error_message":"Покупка недоступна"}))
    except Exception as e:print("answer_pcq",e)
def paid(f,sp):
    x=parse_payload(sp.get("invoice_payload",""));cid=sp.get("telegram_payment_charge_id","")
    if not(x and x[0]==f["id"] and sp.get("currency")=="XTR" and sp.get("total_amount")==x[2] and price_ok(x[1],x[2])):
        for a in ADMINS:say(a,"⚠️ Платёж не прошёл проверку, нужен ручной возврат. Charge ID: %s, от %s, сумма %s"%(cid,f["id"],sp.get("total_amount")))
        return
    u,kind,stars=x
    def go():
        if db.execute("select 1 from pay where charge=?",(cid,)).fetchone():return None
        db.execute("insert into pay(charge,user,kind,stars,ts) values(?,?,?,?,?)",(cid,u,kind,stars,int(time.time())));bal(u)
        if kind=="feathers":add(u,stars*RATE,"stars:%d"%stars);return "✅ Оплата получена! Зачислено %d ★"%(stars*RATE)
        db.execute("update u set mult=max(coalesce(mult,1),?) where id=?",(BOOST[kind][0],u));return "✅ Оплата получена! Клики теперь x%d."%BOOST[kind][0]
    msg=tx(go)
    if msg:say(f["id"],msg)
def check(cb):
    f=cb["from"];touch(f)
    if tx(lambda:banned(f["id"])):tg("answerCallbackQuery",callback_query_id=cb["id"],text="Доступ ограничен.",show_alert=True);return
    if not is_member(f["id"]):
        tg("answerCallbackQuery",callback_query_id=cb["id"],text="Вы ещё не подписаны на канал. Подпишитесь и нажмите «Проверить» снова.",show_alert=True);return
    msg="Отлично, подписка подтверждена! Жми «Играть»."
    with L:
        db.execute("begin immediate")
        try:
            r=claim_sub(f["id"]);db.execute("commit")
            if r.get("claimed"):msg="Отлично, подписка подтверждена! Вам начислено 5 ★. Жми «Играть»."
        except BaseException:db.execute("rollback")
    tg("answerCallbackQuery",callback_query_id=cb["id"])
    tg("sendMessage",chat_id=cb["message"]["chat"]["id"],text=msg,reply_markup=play_kb())
CPS=int(os.environ.get("CPS_MAX","20"));PACK=int(os.environ.get("CLICK_PACK","1000"));REWARD=int(os.environ.get("CLICK_REWARD","500"));CDAY=int(os.environ.get("CLICK_DAILY_MAX","0"))
RATE=int(os.environ.get("STAR_RATE","1"));MIN_STARS=10;MAX_STARS=10000;BOOST={"x2":(2,50),"x3":(3,150)}
SUPPORT=os.environ.get("SUPPORT_CONTACT","администратору бота")
def drop(u,cid,k,v):
    r=db.execute("select first_name,username from u where id=?",(u,)).fetchone();nm=((r[0] or r[1] or "Игрок") if r else "Игрок").strip()
    db.execute("insert into drops(ts,who,case_id,idx,v) values(?,?,?,?,?)",(int(time.time()),nm[:2]+"***",cid,k,v))
    db.execute("delete from drops where id<=(select max(id) from drops)-500")
TB={}
def tokens(u,n):
    now=time.time();t,l=TB.get(u,(float(CPS),now));t=min(float(CPS),t+(now-l)*CPS);k=min(n,int(t+1e-9));TB[u]=(t-k,now);return k
ADMINS={int(x) for x in os.environ.get("ADMIN_IDS","").replace(" ","").split(",") if x.lstrip("-").isdigit()}
if not ADMINS and BACKUP.lstrip("-").isdigit():ADMINS={int(BACKUP)}
def tx(fn):
    with L:
        db.execute("begin immediate")
        try:r=fn();db.execute("commit");return r
        except BaseException:db.execute("rollback");raise
def banned(u):
    r=db.execute("select banned from u where id=?",(u,)).fetchone();return bool(r and r[0])
def say(chat,text,**kw):
    try:tg("sendMessage",chat_id=chat,text=str(text)[:4000],**kw)
    except Exception as e:print("say",e)
def who(a):
    a=a.strip()
    if a.lstrip("-").isdigit():r=db.execute("select id from u where id=?",(int(a),)).fetchone()
    else:r=db.execute("select id from u where lower(username)=lower(?)",(a.lstrip("@"),)).fetchone()
    if not r:raise Err(404,"Игрок не найден: "+a)
    return r[0]
def card(i):
    r=db.execute("select id,username,first_name,balance,banned,ref,sub,refpaid from u where id=?",(i,)).fetchone()
    n=db.execute("select count(*),coalesce(sum(v),0) from inv where user=?",(i,)).fetchone()
    p=db.execute("select count(*),coalesce(sum(stars),0) from pay where user=? and refunded=0",(i,)).fetchone()
    q=db.execute("select count(*),coalesce(sum(refpaid),0) from u where ref=?",(i,)).fetchone()
    return "👤 %s @%s\nID: %d\nБаланс: %d ★\nПредметов: %d (на %d ★)\nПополнил: %d ⭐ (%d раз)\nПодписка: %s\nПригласил его: %s%s\nПриглашено: %d, засчитано: %d\nБан: %s"%(r[2] or "-",r[1] or "-",r[0],r[3],n[0],n[1],p[1],p[0],"да" if r[6] else "нет",r[5] or "-"," (засчитан)" if r[7] else "",q[0],q[1],"ДА" if r[4] else "нет")
def stats():
    r=db.execute("select count(*),coalesce(sum(balance),0),coalesce(sum(banned),0),coalesce(sum(sub),0),coalesce(sum(refpaid),0) from u").fetchone()
    it=db.execute("select count(*),coalesce(sum(v),0) from inv").fetchone();pr=db.execute("select count(*) from promo").fetchone()[0]
    pa=db.execute("select count(*),coalesce(sum(stars),0) from pay where refunded=0").fetchone()
    p24=db.execute("select count(*),coalesce(sum(stars),0) from pay where refunded=0 and ts>?",(int(time.time())-86400,)).fetchone()
    return "📊 Статистика\nИгроков: %d\nПодписаны на канал: %d\nЗабанены: %d\nСумма балансов: %d ★\nПризов в инвентарях: %d (на %d ★)\nПополнений: %d на %d ⭐\nЗа 24 часа: %d на %d ⭐\nРефералов засчитано: %d (выдано %d ★)\nПромокодов: %d"%(r[0],r[3],r[2],r[1],it[0],it[1],pa[0],pa[1],p24[0],p24[1],r[4],r[4]*5,pr)
def redeem(u,code):
    code=code.strip().upper();r=db.execute("select feathers,max_uses,used,expires from promo where code=?",(code,)).fetchone()
    if not r:return "Промокод не найден."
    if r[3] and r[3]<time.time():return "Срок действия промокода истёк."
    if r[1] and r[2]>=r[1]:return "Промокод уже использован максимальное число раз."
    if db.execute("select 1 from promo_used where code=? and user=?",(code,u)).fetchone():return "Вы уже активировали этот промокод."
    db.execute("insert into promo_used values(?,?)",(code,u));db.execute("update promo set used=used+1 where code=?",(code,));bal(u);add(u,r[0],"promo:"+code)
    return "🎉 Промокод активирован! +%d ★"%r[0]
HELP="""🛠 Админ-панель
📊 /stats — статистика (игроки, пополнения, рефералы)
👤 /user ID|@ник — карточка игрока
🔎 /find текст — поиск по имени или нику
🏆 /top [N] — топ по балансу
👥 /refs ID|@ник — кого пригласил игрок
💰 /give ID|@ник N — выдать ★
➖ /take ID|@ник N — забрать ★
✏️ /setbal ID|@ник N — установить баланс
🎁 /giveall N — выдать всем ★
🎒 /items [case_id] — гнёзда и призы
🎁 /giveitem ID|@ник case_id номер — выдать приз
🗑 /clearinv ID|@ник — очистить инвентарь
🔄 /resetdaily ID|@ник — вернуть бесплатное гнездо
🚫 /ban ID|@ник — бан, /unban ID|@ник — разбан, /banned — список
🎟 /promoadd КОД ★ макс_использований [дней] — создать промокод
🎟 /promolist — список, /promodel КОД — удалить
✉️ /msg ID|@ник текст — сообщение игроку
📣 /broadcast текст — рассылка всем, /broadcastplay текст — с кнопкой «Играть»
💳 /payments — оплаты звёздами, /refund CHARGE_ID — вернуть звёзды
💾 /backup — сохранить копию базы сейчас
Игрокам: /promo КОД — активировать промокод.
Баланс ★ — внутриигровая валюта (1 ⭐ при пополнении = 1 ★), вывода нет."""
def admin_tx(cmd,rest,a):
    def N(x):
        try:return int(x)
        except Exception:raise Err(400,"Нужно целое число: "+str(x))
    def need(k):
        if len(a)<k:raise Err(400,"Мало аргументов. Список команд: /admin")
    if cmd in("/admin","/help"):return HELP
    if cmd=="/stats":return stats()
    if cmd=="/user":need(1);return card(who(a[0]))
    if cmd=="/find":
        need(1);q="%"+rest.strip().lower()+"%"
        rows=db.execute("select id,username,first_name,balance from u where lower(coalesce(username,'')) like ? or lower(coalesce(first_name,'')) like ? limit 20",(q,q)).fetchall()
        return "\n".join("%d @%s %s — %d ★"%(r[0],r[1] or "-",r[2] or "-",r[3]) for r in rows) or "Ничего не найдено."
    if cmd=="/top":
        n=N(a[0]) if a else 10
        return "\n".join("%d. %d @%s %s — %d ★"%(k+1,r[0],r[1] or "-",r[2] or "-",r[3]) for k,r in enumerate(db.execute("select id,username,first_name,balance from u order by balance desc limit ?",(max(1,min(50,n)),))))
    if cmd in("/give","/take","/setbal"):
        need(2);i=who(a[0]);n=N(a[1]);cur=bal(i)
        new=cur+n if cmd=="/give" else max(0,cur-n) if cmd=="/take" else max(0,n)
        add(i,new-cur,"admin:"+cmd[1:]);return "Готово. Баланс игрока: %d ★"%bal(i)
    if cmd=="/giveall":
        need(1);n=N(a[0]);db.execute("insert into ledger select id,?,'admin:giveall',? from u where banned=0",(n,int(time.time())));db.execute("update u set balance=balance+? where banned=0",(n,))
        return "Выдано всем: %d ★"%n
    if cmd=="/items":
        if not a:return "Гнёзда: "+", ".join(CASES)
        c=CASES.get(a[0])
        if not c:raise Err(404,"Нет такого гнезда")
        return "\n".join("%d. %s — %d ★"%(k,i["n"],i["v"]) for k,i in enumerate(c["items"]))
    if cmd=="/giveitem":
        need(3);i=who(a[0]);c=CASES.get(a[1])
        if not c:raise Err(404,"Нет такого гнезда (список: /items)")
        k=N(a[2])
        if not 0<=k<len(c["items"]):raise Err(400,"Нет такого номера (список: /items "+a[1]+")")
        db.execute("insert into inv(user,case_id,idx,v) values(?,?,?,?)",(i,c["id"],k,c["items"][k]["v"]));return "Выдано: "+c["items"][k]["n"]
    if cmd in("/givex2","/givex3","/resetmult"):
        need(1);i=who(a[0]);m={"/givex2":2,"/givex3":3,"/resetmult":1}[cmd]
        db.execute("update u set mult=? where id=?",(m,i));return "Игрок %d: клики теперь x%d"%(i,m)
    if cmd=="/clearinv":need(1);i=who(a[0]);db.execute("delete from inv where user=?",(i,));return "Инвентарь очищен."
    if cmd=="/resetdaily":need(1);i=who(a[0]);db.execute("update u set daily=0 where id=?",(i,));return "Бесплатное гнездо снова доступно."
    if cmd=="/addclicks":need(2);i=who(a[0]);db.execute("update u set clicks=max(0,clicks+?) where id=?",(N(a[1]),i));return "Готово."
    if cmd in("/ban","/unban"):
        need(1);i=who(a[0])
        if i in ADMINS:raise Err(400,"Админа банить нельзя")
        db.execute("update u set banned=? where id=?",(1 if cmd=="/ban" else 0,i));return "Игрок %d: %s"%(i,"забанен" if cmd=="/ban" else "разбанен")
    if cmd=="/banned":
        return "\n".join("%d @%s %s"%r for r in db.execute("select id,coalesce(username,'-'),coalesce(first_name,'-') from u where banned=1")) or "Банов нет."
    if cmd=="/promoadd":
        need(3);code=a[0].upper();f=N(a[1]);mx=N(a[2]);days=N(a[3]) if len(a)>3 else 0
        if f<=0 or mx<0 or days<0:raise Err(400,"★ > 0, лимит и дни >= 0 (0 = без лимита)")
        db.execute("insert or replace into promo(code,feathers,max_uses,used,expires) values(?,?,?,0,?)",(code,f,mx,int(time.time()+days*86400) if days else 0))
        return "Промокод %s: +%d ★, лимит %s, срок %s"%(code,f,mx or "∞",("%d дн."%days) if days else "без срока")
    if cmd=="/promolist":
        return "\n".join("%s: +%d ★, использовано %d/%s%s"%(r[0],r[1],r[3],r[2] or "∞"," (истёк)" if r[4] and r[4]<time.time() else "") for r in db.execute("select code,feathers,max_uses,used,expires from promo")) or "Промокодов нет."
    if cmd=="/promodel":
        need(1);db.execute("delete from promo where code=?",(a[0].upper(),));return "Удалено."
    if cmd=="/refs":
        need(1);i=who(a[0])
        tt=db.execute("select count(*),coalesce(sum(refpaid),0) from u where ref=?",(i,)).fetchone()
        rows=db.execute("select id,coalesce(username,'-'),coalesce(first_name,'-'),sub,refpaid from u where ref=? order by rowid desc limit 15",(i,)).fetchall()
        return "👥 Приглашено: %d, засчитано: %d (выдано %d ★)\n"%(tt[0],tt[1],tt[1]*5)+"\n".join("%d @%s %s — %s"%(r[0],r[1],r[2],"засчитан" if r[4] else ("подписан" if r[3] else "не подписан")) for r in rows)
    raise Err(404,"Неизвестная команда. Список: /admin")
ADMIN_CMDS={"/admin","/help","/stats","/user","/find","/top","/give","/take","/setbal","/giveall","/items","/giveitem","/clearinv","/resetdaily","/addclicks","/ban","/unban","/banned","/promoadd","/promolist","/promodel","/msg","/broadcast","/broadcastplay","/backup","/payments","/refund","/refs","/givex2","/givex3","/resetmult"}
def broadcast(ids,text,kb,admin_chat):
    ok=bad=0
    for i in ids:
        try:
            p={"chat_id":i,"text":text}
            if kb:p["reply_markup"]=kb
            tg("sendMessage",**p);ok+=1
        except Exception:bad+=1
        time.sleep(0.05)
    say(admin_chat,"📣 Рассылка завершена. Доставлено: %d, не доставлено: %d"%(ok,bad))
def admin(t,chat):
    p=t.split(None,1);cmd=p[0].split("@")[0].lower();rest=p[1] if len(p)>1 else "";a=rest.split()
    if cmd=="/backup":backup(True);return "💾 Копия базы отправлена."
    if cmd=="/payments":
        def pl():
            t=db.execute("select count(*),coalesce(sum(stars),0) from pay where refunded=0").fetchone()
            rows=db.execute("select charge,user,kind,stars,refunded from pay order by ts desc limit 10").fetchall()
            return "💳 Оплат: %d, звёзд: %d\n"%(t[0],t[1])+"\n".join("%s | user %d | %s | %d ⭐%s"%(r[0],r[1],r[2],r[3]," (возврат)" if r[4] else "") for r in rows)
        return tx(pl)
    if cmd=="/refund":
        if not a:raise Err(400,"Формат: /refund CHARGE_ID")
        r=tx(lambda:db.execute("select user,kind,stars,refunded from pay where charge=?",(a[0],)).fetchone())
        if not r:raise Err(404,"Платёж не найден")
        if r[3]:raise Err(400,"Уже возвращён")
        try:tg("refundStarPayment",user_id=r[0],telegram_payment_charge_id=a[0])
        except Exception as e:raise Err(502,"Telegram не вернул звёзды: %s"%e)
        def undo():
            db.execute("update pay set refunded=1 where charge=?",(a[0],))
            if r[1]=="feathers":
                cur=bal(r[0]);add(r[0],-min(cur,r[2]*RATE),"refund:"+a[0])
            else:
                m=db.execute("select max(case kind when 'x3' then 3 else 2 end) from pay where user=? and refunded=0 and kind in ('x2','x3')",(r[0],)).fetchone()[0] or 1
                db.execute("update u set mult=? where id=?",(m,r[0]))
        tx(undo);return "Звёзды возвращены, покупка отменена."
    if cmd=="/msg":
        if len(a)<2:raise Err(400,"Формат: /msg ID|@ник текст")
        i=tx(lambda:who(a[0]));say(i,rest.split(None,1)[1]);return "Отправлено."
    if cmd in("/broadcast","/broadcastplay"):
        if not rest.strip():raise Err(400,"Формат: /broadcast текст")
        ids=tx(lambda:[r[0] for r in db.execute("select id from u where banned=0")])
        threading.Thread(target=broadcast,args=(ids,rest.strip(),play_kb() if cmd=="/broadcastplay" else None,chat),daemon=True).start()
        return "📣 Рассылка запущена: %d получателей."%len(ids)
    return tx(lambda:admin_tx(cmd,rest,a))
def handle_cmd(f,chat,t):
    try:
        touch(f);cmd=t.split()[0].split("@")[0].lower()
        if cmd=="/terms":return say(chat,"📄 Условия\n• Средний возврат в гнёздах — около 65% от цены гнезда; шансы призов указаны в игре.\n• ★ — внутриигровая валюта (при пополнении 1 Telegram Star = 1 ★). Она не является Telegram Stars, не имеет денежной стоимости, не выводится и не обменивается на деньги или Stars.\n• Звёзды, потраченные на пополнение, не возвращаются, если покупка выдана верно.\n• Выпадение предметов случайно, шансы зависят от гнезда.\n• Если оплата прошла, а покупки нет, напишите /paysupport.")
        if cmd=="/support":return say(chat,"Вопросы по игре: "+SUPPORT)
        if cmd=="/paysupport":return say(chat,"💳 Проблема с оплатой? Напишите "+SUPPORT+": укажите ваш Telegram ID и время оплаты. Если покупка не выдана по нашей вине, мы вернём звёзды.")
        if tx(lambda:banned(f["id"])):return say(chat,"⛔ Доступ ограничен.")
        if f["id"] in ADMINS and cmd in ADMIN_CMDS:
            kb={"inline_keyboard":[[{"text":"📊 Статистика","callback_data":"adm_stats"},{"text":"🏆 Топ","callback_data":"adm_top"}],[{"text":"💳 Оплаты","callback_data":"adm_pay"},{"text":"🎟 Промокоды","callback_data":"adm_promo"}]]} if cmd in("/admin","/help") else None
            return say(chat,admin(t,chat),**({"reply_markup":kb} if kb else {}))
        if cmd=="/promo":
            if not is_member(f["id"]):return welcome(chat,f)
            p=t.split(None,1)
            return say(chat,tx(lambda:redeem(f["id"],p[1])) if len(p)>1 else "Напишите: /promo КОД")
    except Err as e:say(chat,e.msg)
    except Exception as e:print("cmd",e)
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
    if r[0]:return {"claimed":False,"sub":True,"balance":bal(u)}
    try:st=tg("getChatMember",chat_id=CHANNEL,user_id=u)["status"]
    except Exception:raise Err(502,"tg_error")
    if st not in("member","administrator","creator"):return {"claimed":False,"sub":False,"balance":bal(u)}
    db.execute("update u set sub=1 where id=?",(u,));add(u,5,"sub")
    if r[1] and not r[2]:
        db.execute("update u set refpaid=1 where id=?",(u,))
        if db.execute("select 1 from u where id=?",(r[1],)).fetchone():add(r[1],5,"referral:%d"%u)
    return {"claimed":True,"sub":True,"balance":bal(u)}
NICKTAG=os.environ.get("NICK_TAG","@TukanBattle_bot").lower()
def claim_nick(u):
    r=db.execute("select nick from u where id=?",(u,)).fetchone()
    if r[0]:return {"claimed":False,"nick":True,"balance":bal(u)}
    try:ch=tg("getChat",chat_id=u)
    except Exception:raise Err(502,"tg_error")
    name=((ch.get("first_name") or "")+" "+(ch.get("last_name") or "")).lower()
    if NICKTAG not in name:return {"claimed":False,"nick":False,"balance":bal(u)}
    db.execute("update u set nick=1 where id=?",(u,));add(u,5,"nick")
    return {"claimed":True,"nick":True,"balance":bal(u)}
PM=[11,4,1.4,1,0.35,0.2,0.35,1,1.4,4,11]
def emult(k,m):
    x=0.65
    for i in range(k):x*=(25-i)/(25-m-i)
    return x
ITEMS={}
for _c in CASES.values():
    if _c["cost"]:
        for _k,_i in enumerate(_c["items"]):ITEMS.setdefault(_i["n"],(_c["id"],_k,_i["v"]))
class Err(Exception):
    def __init__(s,code,msg):s.code=code;s.msg=msg
def invite(u):
    if not BOTNAME[0]:raise Err(503,"no_bot")
    link="https://t.me/%s?start=ref_%d"%(BOTNAME[0],u)
    who=CHANNEL if CHANNEL.startswith("@") else "Тукан Батл"
    text="Заходи на %s и выигрывай лучшие призы! 🦜"%who
    out={"referral_link":link,"text":text}
    try:
        res={"type":"article","id":"inv%d"%u,"title":"Тукан Батл","description":"Приглашение в игру","input_message_content":{"message_text":text},"reply_markup":{"inline_keyboard":[[{"text":"▶️ Играть","url":link}]]}}
        out["message_id"]=tg("savePreparedInlineMessage",user_id=u,result=res,allow_user_chats=True,allow_group_chats=True,allow_channel_chats=False,allow_bot_chats=False)["id"]
    except Exception as e:print("prepared",e)
    return out
def act(u,path,b):
    if path.startswith("/api/clicker/") or path.startswith("/api/battle/"):raise Err(403,"closed")
    rid=str(b.get("request_id",""))[:64]
    if path=="/api/me":return {"balance":bal(u),"inventory":invlist(u),"clicks":(db.execute("select clicks from u where id=?",(u,)).fetchone() or [0])[0],"mult":(db.execute("select mult from u where id=?",(u,)).fetchone() or [1])[0] or 1,"rate":RATE,"sub":bool((db.execute("select sub from u where id=?",(u,)).fetchone() or [0])[0]),"nick":bool((db.execute("select nick from u where id=?",(u,)).fetchone() or [0])[0]),"referral_link":("https://t.me/%s?start=ref_%d"%(BOTNAME[0],u)) if BOTNAME[0] else None}
    if path=="/api/referral-link":
        if not BOTNAME[0]:raise Err(503,"no_bot")
        return {"referral_link":"https://t.me/%s?start=ref_%d"%(BOTNAME[0],u)}
    if path=="/api/claim-subscription":bal(u);return claim_sub(u)
    if path=="/api/claim-nick":bal(u);return claim_nick(u)
    if path=="/api/leaderboard":
        top=db.execute("select id,username,first_name,balance from u order by balance desc limit 20").fetchall();b=bal(u)
        return {"players":[{"username":x[1],"first_name":x[2],"balance":x[3],"is_me":x[0]==u} for x in top],"me":{"rank":db.execute("select count(*)+1 from u where balance>?",(b,)).fetchone()[0],"balance":b}}
    if path=="/api/crash/state":
        r=db.execute("select cp,t0 from crash where user=?",(u,)).fetchone()
        if not r:return {"none":True}
        if math.exp(.22*max(0,time.time()-r[1]-.25))>=r[0]:db.execute("delete from crash where user=?",(u,));return {"crashed":True,"cp":r[0]}
        return {"crashed":False}
    if path=="/api/clicker/tap":
        n=max(0,min(CPS,int(b.get("n",0))));k=tokens(u,n) if n else 0;bal(u)
        if k and CDAY:
            d=time.strftime("%Y-%m-%d");r=db.execute("select ckday,ckcnt from u where id=?",(u,)).fetchone()
            c=r[1] if r[0]==d else 0;k=max(0,min(k,CDAY-c));db.execute("update u set ckday=?,ckcnt=? where id=?",(d,c+k,u))
        m=db.execute("select mult from u where id=?",(u,)).fetchone()[0] or 1
        if k:db.execute("update u set clicks=clicks+? where id=?",(k*m,u))
        return {"accepted":k,"mult":m,"clicks":db.execute("select clicks from u where id=?",(u,)).fetchone()[0],"daily_limit":CDAY}
    if path=="/api/drops":
        since=int(b.get("since",0));rows=db.execute("select id,who,case_id,idx,v from drops where id>? order by id desc limit 30",(since,)).fetchall()[::-1]
        t=db.execute("select who,case_id,idx,v from drops where ts>? order by v desc,id desc limit 1",(time.time()-86400,)).fetchone()
        return {"drops":[{"id":r[0],"who":r[1],"case":r[2],"i":r[3],"v":r[4]} for r in rows],"top":({"who":t[0],"case":t[1],"i":t[2],"v":t[3]} if t else None)}
    if path=="/api/shop/invoice":
        kind=b.get("kind");bal(u)
        if kind=="feathers":
            stars=int(b.get("stars",0))
            if not MIN_STARS<=stars<=MAX_STARS:raise Err(400,"bad_amount")
            title,desc="%d ★ на баланс"%(stars*RATE),"Пополнение игрового баланса. ★ — внутриигровая валюта: вывод и обмен невозможны."
        elif kind in ():
            mm,stars=BOOST[kind]
            if (db.execute("select mult from u where id=?",(u,)).fetchone()[0] or 1)>=mm:raise Err(409,"already_owned")
            title,desc="Клики x%d"%mm,"Каждый клик в кликере считается за %d"%mm
        else:raise Err(400,"bad_request")
        try:link=tg("createInvoiceLink",title=title,description=desc,payload="p1|%d|%s|%d|%s"%(u,kind,stars,secrets.token_hex(4)),currency="XTR",prices=[{"label":title,"amount":stars}])
        except Exception as e:print("invoice",e);raise Err(502,"tg_error")
        return {"link":link}
    if not rid:raise Err(400,"bad_request")
    old=db.execute("select resp from req where user=? and rid=?",(u,rid)).fetchone()
    if old:return json.loads(old[0])
    bal(u);out=None
    if path=="/api/case/open":
        c=CASES.get(b.get("case_id"));n=int(b.get("count",1))
        if not c or not 1<=n<=10:raise Err(400,"bad_request")
        if c["cost"]==0:
            n=1
            now=int(time.time())
            if db.execute("update u set daily=? where id=? and ?-daily>=?",(now,u,now,DAY)).rowcount!=1:raise Err(409,"daily_used")
        if bal(u)<c["cost"]*n:raise Err(402,"insufficient_funds")
        add(u,-c["cost"]*n,"open:"+c["id"]);got=[]
        for _ in range(n):
            k=pick(c);v=c["items"][k]["v"]
            cur=db.execute("insert into inv(user,case_id,idx,v) values(?,?,?,?)",(u,c["id"],k,v))
            drop(u,c["id"],k,v);got.append({"uid":cur.lastrowid,"i":k,"v":v})
        out={"items":got}
    elif path=="/api/inventory/sell":
        ids=[r[0] for r in db.execute("select uid from inv where user=?",(u,))] if b.get("all") else [int(x) for x in b.get("uids",[])]
        tot=0
        for i in ids:
            r=db.execute("select v from inv where uid=? and user=?",(i,u)).fetchone()
            if r:tot+=r[0];db.execute("delete from inv where uid=?",(i,))
        add(u,tot,"sell");out={"sold":tot}
    elif path=="/api/clicker/buy":
        packs=int(b.get("packs",1))
        if not 1<=packs<=10000:raise Err(400,"bad_request")
        have=db.execute("select clicks from u where id=?",(u,)).fetchone()[0]
        if have<packs*PACK:raise Err(402,"not_enough_clicks")
        db.execute("update u set clicks=clicks-? where id=?",(packs*PACK,u));add(u,packs*REWARD,"clicker");out={"clicks":have-packs*PACK}
    elif path=="/api/crash/start":
        bet=int(b.get("bet",0))
        if not 10<=bet<=100000:raise Err(400,"bad_request")
        if bal(u)<bet:raise Err(402,"insufficient_funds")
        cp=min(100,max(1,0.65/(1-secrets.randbelow(10**9)/10**9)));cp=math.floor(cp*100)/100
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
        if t<10 or not tv>t*1.05 or tv>t*80:raise Err(400,"bad_request")
        if bal(u)<f:raise Err(402,"insufficient_funds")
        ch=min(75,t/tv*65);win=secrets.randbelow(10**6)/10**4<ch
        add(u,-f,"upgrade")
        for x in uids:db.execute("delete from inv where uid=?",(x,))
        nid=None
        if win:cid,k,v=ITEMS[tn];drop(u,cid,k,v);nid=db.execute("insert into inv(user,case_id,idx,v) values(?,?,?,?)",(u,cid,k,v)).lastrowid
        out={"win":win,"chance":ch,"uid":nid}
    elif path=="/api/battle/play":
        c=CASES.get(b.get("case_id"))
        if not c or c["cost"]<=0:raise Err(400,"bad_request")
        if bal(u)<c["cost"]:raise Err(402,"insufficient_funds")
        add(u,-c["cost"],"battle");ia,ib=pick(c),pick(c);va,vb=c["items"][ia]["v"],c["items"][ib]["v"];uids=[None,None]
        def put(k,v):
            drop(u,c["id"],k,v);return db.execute("insert into inv(user,case_id,idx,v) values(?,?,?,?)",(u,c["id"],k,v)).lastrowid
        if va>vb:uids=[put(ia,va),put(ib,vb)]
        elif va==vb:uids=[put(ia,va),None]
        out={"a":ia,"b":ib,"uids":uids}
    elif path=="/api/plinko/drop":
        bet=int(b.get("bet",0))
        if not 10<=bet<=100000:raise Err(400,"bad_request")
        if bal(u)<bet:raise Err(402,"insufficient_funds")
        ks=[0]
        for _ in range(10):ks.append(ks[-1]+secrets.randbelow(2))
        win=math.floor(bet*PM[ks[-1]]);add(u,-bet,"plinko");add(u,win,"plinko_win");out={"ks":ks,"win":win}
    elif path=="/api/mines/start":
        bet=int(b.get("bet",0));n=int(b.get("mines",0))
        if not(10<=bet<=100000 and 2<=n<=24):raise Err(400,"bad_request")
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
        with L:bn=banned(u)
        if bn:return s.send(403,{"error":"banned"})
        if not is_member(u):return s.send(403,{"error":"not_subscribed","channel":CLINK})
        if s.path.split("?")[0]=="/api/invite-message":
            try:return s.send(200,invite(u))
            except Err as e:return s.send(e.code,{"error":e.msg})
        try:
            with L:
                db.execute("begin immediate")
                try:r=act(u,s.path.split("?")[0],b);db.execute("commit")
                except BaseException:db.execute("rollback");raise
            s.send(200,r)
        except Err as e:s.send(e.code,{"error":e.msg})
        except (ValueError,TypeError,KeyError,AttributeError,OverflowError):s.send(400,{"error":"bad_request"})
        except Exception:s.send(500,{"error":"server"})
    def log_message(s,*a):pass
def bye(*a):backup(True);sys.exit(0)
if __name__=="__main__":
    if TOKEN:threading.Thread(target=poll,daemon=True).start();threading.Thread(target=backup_loop,daemon=True).start()
    signal.signal(signal.SIGTERM,bye)
    ThreadingHTTPServer(("0.0.0.0",int(os.environ.get("PORT",8080))),H).serve_forever()
