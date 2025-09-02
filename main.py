import argparse
import socket
import ssl
import sys
import time
import threading

parser = argparse.ArgumentParser(description="Connect to an IRC server.")
parser.add_argument("--server", default="irc.libera.chat", help="IRC server address")
parser.add_argument("--port", type=int, default=6697, help="IRC server port")
parser.add_argument("--nickname", default="Guest", help="Your IRC nickname")
parser.add_argument("--password", default="", help="Password for the IRC server (if any)")
parser.add_argument("--channel", default="##chat", help="Channel to join")

args = parser.parse_args()

server = args.server
port = args.port
nickname = args.nickname
password = args.password
channel = args.channel

sock = socket.socket()
irc = ssl.create_default_context().wrap_socket(sock, server_hostname=server)
irc.connect((server, port))

print(f"Connected to {server}:{port} as {nickname}, joining {channel}")

connected = False
joined_channels = set()
current_channel = channel
input_buffer = ""
prompt = channel + "> "

recent_sent = []

def b_hash(s):
    h = 0
    for c in s:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF

    return h

PALETTE = [31, 32, 33, 34, 35, 36, 91, 92, 93, 94, 95, 96, 37, 90, 97]

def color_hash(text):
    idx = b_hash(text) % len(PALETTE)
    return f"< \033[{PALETTE[idx]}m{text}\033[0m >"

print_queue = []
stdout_lock = threading.Lock()
_last_len = 0

def _redraw():
    global _last_len
    line = f"\r\033[2K{prompt}{input_buffer}"
    pad = max(0, _last_len - len(prompt + input_buffer))
    if pad:
        line += " " * pad
    sys.stdout.write(line)
    sys.stdout.flush()
    _last_len = len(prompt + input_buffer)

def printer():
    global print_queue
    while True:
        if not print_queue:
            time.sleep(0.05)
            continue
        typ, payload = print_queue.pop(0)
        if typ == "stop":
            break
        if typ == "message":
            with stdout_lock:
                sys.stdout.write("\r\033[2K" + payload + "\n")
                sys.stdout.flush()
                _redraw()
        elif typ == "redraw":
            with stdout_lock:
                _redraw()

def print_message(msg):
    print_queue.append(("message", msg))

def send_raw(line):
    try:
        irc.sendall(line.encode("utf-8"))
    except Exception as e:
        print_message("[send error] " + str(e))

send_raw(f"NICK {nickname}\r\n")
send_raw(f"USER {nickname} 0 * :{nickname}\r\n")

def parse_message(line):
    prefix, command, args = None, None, []
    if not line: return None, None, []
    if line.startswith(":"):
        parts = line[1:].split(" ", 1)
        if len(parts) == 2:
            prefix, line = parts
    if " " in line:
        command, rest = line.split(" ", 1)
        if " :" in rest:
            pre, trailing = rest.split(" :", 1)
            args = pre.split() + [trailing]
        else:
            args = rest.split()
    else:
        command = line
    return prefix, command, args

def listen():
    global connected, nickname, current_channel, prompt
    buf = ""
    while True:
        try:
            data = irc.recv(4096)
            if not data:
                print_message("Disconnected.")
                break
            buf += data.decode("utf-8", "ignore")
        except:
            break

        while "\r\n" in buf:
            line, buf = buf.split("\r\n", 1)
            if line.startswith("PING"):
                token = line.split(" ",1)[1] if " " in line else ""
                send_raw("PONG " + token + "\r\n")
                continue

            prefix, cmd, args = parse_message(line)

            if cmd == "001":   
                connected = True
                print_message("Connected.")
                if password:
                    send_raw(f"PRIVMSG NickServ :IDENTIFY {password}\r\n")
                    time.sleep(0.3)
                send_raw(f"JOIN {current_channel}\r\n")
                joined_channels.add(current_channel)

            elif cmd == "433":   
                nickname = nickname + "_" + str(int(time.time()) % 1000)
                send_raw(f"NICK {nickname}\r\n")

            elif cmd == "PRIVMSG":
                who = prefix.split("!")[0] if prefix else "?"
                target, msg = args[0], args[-1]
                if target.lower() == nickname.lower():
                    print_message(f"[PM] {color_hash(who)} {msg}")
                else:
                    print_message(f"{color_hash(who)} {msg}")

            elif cmd == "JOIN":
                who = prefix.split("!")[0]
                chan = args[0]
                if who.lower() == nickname.lower():
                    print_message("*** joined " + chan)
                    joined_channels.add(chan)
                else:
                    print_message(f"*** {color_hash(who)} joined {chan}")

            elif cmd == "PART":
                who = prefix.split("!")[0]
                chan = args[0]
                print_message(f"*** {color_hash(who)} left {chan}")
                if who.lower() == nickname.lower() and chan in joined_channels:
                    joined_channels.remove(chan)

            elif cmd == "QUIT":
                who = prefix.split("!")[0]
                msg = args[0] if args else ""
                print_message(f"*** {color_hash(who)} quit ({msg})")

            elif cmd == "NOTICE":
                who = prefix.split("!")[0] if prefix else ""
                msg = args[-1] if args else ""
                print_message(f"[NOTICE] {who}: {msg}")

            elif cmd and cmd.isdigit():
                print_message("<< " + cmd + " " + " ".join(args))

def read_char():
    return sys.stdin.read(1)

def send_loop():
    global input_buffer, prompt, current_channel, nickname
    print_message("Connecting...")
    while not connected:
        time.sleep(0.1)
    print_queue.append(("redraw", None))

    while True:
        ch = read_char()
        if ch == "\n":
            msg = input_buffer.strip()
            input_buffer = ""
            print_queue.append(("redraw", None))

            if not msg:
                continue

            if msg.lower() == "/quit":
                send_raw("QUIT :bye\r\n")
                print_queue.append(("stop", None))
                sys.exit(0)

            elif msg.startswith("/join "):
                chan = msg.split(" ", 1)[1].strip()
                send_raw(f"JOIN {chan}\r\n")
                current_channel = chan
                prompt = chan + "> "
                print_queue.append(("redraw", None))

            elif msg.startswith("/part "):
                chan = msg.split(" ", 1)[1].strip()
                send_raw(f"PART {chan}\r\n")
                if chan in joined_channels:
                    joined_channels.remove(chan)

            elif msg.startswith("/nick "):
                nickname = msg.split(" ", 1)[1].strip()
                send_raw(f"NICK {nickname}\r\n")

            elif msg.startswith("/msg "):
                parts = msg.split(" ", 2)
                if len(parts) >= 3:
                    target, message = parts[1], parts[2]
                    send_raw(f"PRIVMSG {target} :{message}\r\n")
                    print_message(f"[PM -> {color_hash(target)}] {message}")
                else:
                    print_message("Usage: /msg <nick> <message>")

            elif msg.startswith("/me "):
                action = msg[4:].strip()
                if current_channel and action:
                    send_raw(f"PRIVMSG {current_channel} :\x01ACTION {action}\x01\r\n")
                    print_message(f"* {nickname} {action}")

            elif msg.startswith("/topic "):
                topic = msg[7:].strip()
                if current_channel and topic:
                    send_raw(f"TOPIC {current_channel} :{topic}\r\n")

            elif msg.startswith("/notice "):
                parts = msg.split(" ", 2)
                if len(parts) >= 3:
                    target, message = parts[1], parts[2]
                    send_raw(f"NOTICE {target} :{message}\r\n")
                    print_message(f"[NOTICE -> {color_hash(target)}] {message}")
                else:
                    print_message("Usage: /notice <nick> <message>")

            else:
                if current_channel:
                    send_raw(f"PRIVMSG {current_channel} :{msg}\r\n")
                    print_message(f"{color_hash(nickname)} {msg}")

        elif ch in ("\x7f", "\b"):
            if input_buffer:
                input_buffer = input_buffer[:-1]
            print_queue.append(("redraw", None))
        else:
            input_buffer += ch
            print_queue.append(("redraw", None))


threading.Thread(target=printer, daemon=True).start()
threading.Thread(target=listen, daemon=True).start()
send_loop()
