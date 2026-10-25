import asyncio, json, os, subprocess, sys, time
from urllib.request import urlopen
import websockets

ROOT=os.path.dirname(__file__)
PORT=18765
proc=subprocess.Popen([sys.executable,'-m','uvicorn','server:app','--host','127.0.0.1','--port',str(PORT)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

async def recv(ws, expected='state'):
    for _ in range(6):
        raw=await asyncio.wait_for(ws.recv(),timeout=3)
        msg=json.loads(raw)
        if msg['type']==expected: return msg
        if msg['type']=='error': raise AssertionError(msg['message'])
    raise AssertionError('Expected message '+expected)

async def main():
    try:
        for _ in range(40):
            try:
                urlopen(f'http://127.0.0.1:{PORT}/health',timeout=.2).read()
                break
            except Exception: await asyncio.sleep(.15)
        else: raise RuntimeError('server did not start')
        uri=f'ws://127.0.0.1:{PORT}/ws'
        async with websockets.connect(uri) as host, websockets.connect(uri) as guest:
            await host.send(json.dumps({'type':'create_room','client_id':'host-test','player_name':'Host','settings':{'startPrice':10,'maxTurns':5,'startCash':200,'startStocks':10,'difficulty':'Normal','volatility':True}}))
            hs=await recv(host)
            code=hs['room']['code']; assert hs['room']['status']=='lobby'
            await guest.send(json.dumps({'type':'join_room','client_id':'guest-test','player_name':'Guest','room_code':code}))
            hs=await recv(host); gs=await recv(guest)
            assert len(hs['room']['players'])==2 and len(gs['room']['players'])==2
            await host.send(json.dumps({'type':'start_game'}))
            hs=await recv(host); gs=await recv(guest)
            assert hs['room']['status']=='in_game' and gs['game']['turnIndex']==0
            await host.send(json.dumps({'type':'action','action':'buy','quantity':1}))
            hs=await recv(host); gs=await recv(guest)
            assert hs['game']['turn']==1 and hs['game']['turnIndex']==1
            assert hs['game']['players'][0]['stocks']==11
            await host.send(json.dumps({'type':'action','action':'buy','quantity':1}))
            err=await recv(host, expected='error'); assert 'turn' in err['message'].lower()
            await guest.send(json.dumps({'type':'action','action':'skip','quantity':0}))
            hs=await recv(host); gs=await recv(guest)
            assert hs['game']['turn']==2 and hs['game']['turnIndex']==0
            print('PASS: room create/join, shared lobby, host start, synced buy/skip, wrong-turn rejection; room code:',code)
    finally:
        proc.terminate()
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired: proc.kill()

asyncio.run(main())
