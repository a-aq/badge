const express = require('express');
const WebSocket = require('ws');
const dgram = require('dgram');
const { spawn } = require('child_process');
const nacl = require('tweetnacl');
const { OpusEncoder } = require('@discordjs/opus');
const ffmpegStatic = require('ffmpeg-static');

// --- 1. WEB SERVER FOR RA3D STUDIO ---
const app = express();
app.get('/', (req, res) => {
    res.send("Bot is awake and running!");
});

const PORT = process.env.PORT || 10000;
app.listen(PORT, () => {
    console.log(`Web server listening on port ${PORT}`);
});

// --- 2. DISCORD BOT CONFIGURATION ---
const TOKEN = process.env.DISCORD_TOKEN; // Set this in Render Environment Variables
const GUILD_ID = "1286784355733209129";
const VOICE_CHANNEL_ID = "1286784356584788145";
const AUDIO_FILE = "music.mp3"; // The exact name of your audio file

// --- 3. DISCORD BOT LOGIC ---
class DiscordVoiceBot {
    constructor(token) {
        this.token = token;
        this.encoder = new OpusEncoder(48000, 2);
        this.udp = dgram.createSocket('udp4');
    }

    connectMain() {
        console.log("Connecting to Main Gateway...");
        this.ws = new WebSocket("wss://gateway.discord.gg?v=10");

        this.ws.on('message', async (data) => {
            const payload = JSON.parse(data);
            const { op, t, d } = payload;

            if (op === 10) { 
                this.ws.send(JSON.stringify({
                    op: 10,
                    d: { token: this.token, intents: 0, properties: { os: "linux", browser: "render", device: "render" } }
                }));
                setInterval(() => { this.ws.send(JSON.stringify({ op: 1, d: null })); }, d.heartbeat_interval);
            }

            if (t === 'READY') {
                this.userId = d.user.id;
                this.sessionId = d.session_id;
                console.log("Logged in! Setting status and joining voice channel...");

                // Fake Game Status
                this.ws.send(JSON.stringify({
                    op: 3,
                    d: { since: null, game: { name: "Clash Royale", type: 0 }, status: "dnd", afk: false }
                }));

                // Join Voice
                this.ws.send(JSON.stringify({
                    op: 4,
                    d: { guild_id: GUILD_ID, channel_id: VOICE_CHANNEL_ID, self_mute: false, self_deaf: false }
                }));
            }

            if (t === 'VOICE_SERVER_UPDATE') {
                this.connectVoice(d.endpoint.replace(':80', ''), d.token);
            }
        });
    }

    connectVoice(endpoint, token) {
        this.voiceWs = new WebSocket(`wss://${endpoint}?v=4`);
        this.voiceWs.on('message', async (data) => {
            const { op, d } = JSON.parse(data);

            if (op === 8) {
                setInterval(() => { this.voiceWs.send(JSON.stringify({ op: 3, d: Date.now() })); }, d.heartbeat_interval);
                this.voiceWs.send(JSON.stringify({ op: 0, d: { server_id: GUILD_ID, user_id: this.userId, session_id: this.sessionId, token: token } }));
            }
            if (op === 2) {
                this.ssrc = d.ssrc; this.voiceIp = d.ip; this.voicePort = d.port;
                this.performIpDiscovery();
            }
            if (op === 4) {
                this.secretKey = new Uint8Array(d.secret_key);
                console.log("Secret Key obtained! Starting audio stream...");
                this.streamAudio(AUDIO_FILE);
            }
        });
    }

    performIpDiscovery() {
        const packet = Buffer.alloc(74);
        packet.writeUInt16BE(1, 0); packet.writeUInt16BE(70, 2); packet.writeUInt32BE(this.ssrc, 4);
        this.udp.once('message', (msg) => {
            const localIp = msg.toString('utf8', 8, msg.indexOf(0, 8));
            const localPort = msg.readUInt16BE(72);
            this.voiceWs.send(JSON.stringify({ op: 1, d: { protocol: "udp", data: { address: localIp, port: localPort, mode: "xsalsa20_poly1305" } } }));
        });
        this.udp.send(packet, this.voicePort, this.voiceIp);
    }

    streamAudio(filepath) {
        // Using the downloaded ffmpeg-static instead of requiring system ffmpeg
        const ffmpeg = spawn(ffmpegStatic, ['-i', filepath, '-f', 's16le', '-ar', '48000', '-ac', '2', '-loglevel', 'warning', 'pipe:1']);
        let sequence = 0, timestamp = 0, nextTime = Date.now();

        const tick = () => {
            const pcmData = ffmpeg.stdout.read(3840);
            
            // Wait if FFmpeg is still processing
            if (!pcmData) { setTimeout(tick, 2); return; }
            
            // If the audio file ends, restart it in a loop
            if (pcmData.length !== 3840) { 
                console.log("Audio finished. Looping back to the start...");
                ffmpeg.kill(); 
                this.streamAudio(filepath); 
                return; 
            }

            const opusData = this.encoder.encode(pcmData);
            const header = Buffer.alloc(12);
            header[0] = 0x80; header[1] = 0x78;
            header.writeUInt16BE(sequence, 2); header.writeUInt32BE(timestamp, 4); header.writeUInt32BE(this.ssrc, 8);

            const nonce = Buffer.alloc(24); header.copy(nonce, 0, 0, 12);
            const encryptedData = nacl.secretbox(new Uint8Array(opusData), new Uint8Array(nonce), this.secretKey);

            this.udp.send(Buffer.concat([header, Buffer.from(encryptedData)]), this.voicePort, this.voiceIp);
            sequence = (sequence + 1) % 65535; timestamp = (timestamp + 960) % 4294967295;
            
            nextTime += 20;
            setTimeout(tick, Math.max(0, nextTime - Date.now()));
        };
        ffmpeg.stdout.once('readable', tick);
    }
}

// Start the bot
const bot = new DiscordVoiceBot(TOKEN);
bot.connectMain();
