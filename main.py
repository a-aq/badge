import asyncio
import aiohttp
import json
import socket
import struct
import subprocess
import time
import nacl.secret
import opuslib
import os

# --- CONFIGURATION ---
TOKEN = os.environ.get("DISCORD_TOKEN", "NzczODA0NDg2MTYyMzgyODU4.G0Jnd3.15PkVrG7tSVghSU_RMCSWcg05cG0EVI7XOqXUs")
GUILD_ID = 1286784355733209129 
VOICE_CHANNEL_ID = 1286784356584788145
AUDIO_FILE = "music.mp3"  # Make sure this file is uploaded to your repo
# ---------------------

class DiscordVoiceBot:
    def __init__(self, token):
        self.token = token
        self.session_id = None
        self.user_id = None
        self.ws = None
        self.voice_ws = None
        self.udp_socket = None
        self.ssrc = None
        self.secret_key = None
        self.voice_ip = None
        self.voice_port = None
        self.encoder = opuslib.Encoder(48000, 2, 'audio')

    async def connect_main(self):
        print("Connecting to Main Gateway...")
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect("wss://gateway.discord.gg?v=10")
        asyncio.create_task(self.listen_main())

    async def send_main(self, op, data):
        await self.ws.send_str(json.dumps({"op": op, "d": data}))

    async def listen_main(self):
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                op, t, d = data.get('op'), data.get('t'), data.get('d')

                if op == 10: 
                    await self.send_main(10, {
                        "token": self.token,
                        "intents": 0,
                        "properties": {"os": "linux", "browser": "render", "device": "render"}
                    })
                    asyncio.create_task(self.heartbeat_loop(self.ws, d['heartbeat_interval']))

                elif t == 'READY':
                    self.user_id = d['user']['id']
                    self.session_id = d['session_id']
                    print("Logged in. Setting status to Playing Clash Royale.")
                    
                    # Set Status
                    await self.send_main(3, {
                        "since": None,
                        "game": {"name": "Clash Royale", "type": 0},
                        "status": "dnd", 
                        "afk": False
                    })
                    
                    # Join Voice
                    await self.send_main(4, {
                        "guild_id": str(GUILD_ID),
                        "channel_id": str(VOICE_CHANNEL_ID),
                        "self_mute": False, "self_deaf": False
                    })

                elif t == 'VOICE_SERVER_UPDATE':
                    voice_token = d['token']
                    endpoint = d['endpoint'].replace(':80', '') 
                    asyncio.create_task(self.connect_voice(endpoint, voice_token))

    async def connect_voice(self, endpoint, token):
        print(f"Connecting to Voice WebSocket: wss://{endpoint}?v=4")
        self.voice_ws = await self.session.ws_connect(f"wss://{endpoint}?v=4")
        
        await self.voice_ws.send_str(json.dumps({
            "op": 0,
            "d": {
                "server_id": str(GUILD_ID),
                "user_id": self.user_id,
                "session_id": self.session_id,
                "token": token
            }
        }))
        asyncio.create_task(self.listen_voice())

    async def listen_voice(self):
        async for msg in self.voice_ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                op, d = data.get('op'), data.get('d')

                if op == 8: 
                    asyncio.create_task(self.heartbeat_loop(self.voice_ws, d['heartbeat_interval'], is_voice=True))

                elif op == 2: 
                    self.ssrc = d['ssrc']
                    self.voice_ip = d['ip']
                    self.voice_port = d['port']
                    await self.perform_ip_discovery()

                elif op == 4: 
                    self.secret_key = d['secret_key']
                    print("SUCCESS! Starting audio stream...")
                    asyncio.create_task(self.stream_audio(AUDIO_FILE))

    async def perform_ip_discovery(self):
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_socket.setblocking(False)
        
        packet = struct.pack('>HHI66x', 1, 70, self.ssrc)
        self.udp_socket.sendto(packet, (self.voice_ip, self.voice_port))

        loop = asyncio.get_running_loop()
        data, _ = await loop.sock_recvfrom(self.udp_socket, 74)
        
        local_ip = data[8:72].strip(b'\x00').decode('utf-8')
        local_port = struct.unpack('>H', data[72:74])[0]

        await self.voice_ws.send_str(json.dumps({
            "op": 1,
            "d": {
                "protocol": "udp",
                "data": {"address": local_ip, "port": local_port, "mode": "xsalsa20_poly1305"}
            }
        }))

    async def stream_audio(self, filepath):
        box = nacl.secret.SecretBox(bytes(self.secret_key))
        cmd = ['ffmpeg', '-i', filepath, '-f', 's16le', '-ar', '48000', '-ac', '2', '-loglevel', 'warning', 'pipe:1']
        ffmpeg = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        
        sequence = 0
        timestamp = 0
        start_time = time.time()
        loops = 0
        
        try:
            while True:
                pcm_data = ffmpeg.stdout.read(3840)
                if len(pcm_data) != 3840:
                    print("Finished playing audio.")
                    break
                    
                opus_data = self.encoder.encode(pcm_data, 960)
                header = bytearray(12)
                header[0] = 0x80
                header[1] = 0x78
                struct.pack_into('>H', header, 2, sequence)
                struct.pack_into('>I', header, 4, timestamp)
                struct.pack_into('>I', header, 8, self.ssrc)
                
                nonce = bytearray(24)
                nonce[:12] = header
                encrypted_data = box.encrypt(bytes(opus_data), bytes(nonce)).ciphertext
                
                self.udp_socket.sendto(header + encrypted_data, (self.voice_ip, self.voice_port))
                
                sequence = (sequence + 1) % 65535
                timestamp = (timestamp + 960) % 4294967295
                loops += 1
                
                next_time = start_time + (20 * loops) / 1000.0
                delay = next_time - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                    
        finally:
            ffmpeg.kill()

    async def heartbeat_loop(self, ws, interval, is_voice=False):
        while not ws.closed:
            await asyncio.sleep(interval / 1000)
            payload = {"op": 3, "d": int(time.time() * 1000)} if is_voice else {"op": 1, "d": None}
            await ws.send_str(json.dumps(payload))

async def main():
    bot = DiscordVoiceBot(TOKEN)
    await bot.connect_main()
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
