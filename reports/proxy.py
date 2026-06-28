#!/usr/bin/env python3
"""Простой HTTP/HTTPS forward прокси (CONNECT support)"""
import socket
import threading
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 3128
BUFSIZE = 65536

def handle_client(client):
    try:
        req = client.recv(BUFSIZE)
        if not req:
            return
        
        first_line = req.split(b'\r\n')[0].decode('utf-8', errors='replace')
        parts = first_line.split()
        
        if len(parts) < 2:
            client.close()
            return
        
        method = parts[0]
        target = parts[1]
        
        if method == 'CONNECT':
            # HTTPS CONNECT tunnel
            host_port = target
            host = host_port.rsplit(':', 1)[0]
            port = int(host_port.rsplit(':', 1)[1])
            
            try:
                remote = socket.create_connection((host, port), timeout=15)
                client.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                
                # Bidirectional tunnel
                def forward(src, dst):
                    try:
                        while True:
                            data = src.recv(BUFSIZE)
                            if not data:
                                break
                            dst.sendall(data)
                    except:
                        pass
                
                t1 = threading.Thread(target=forward, args=(client, remote), daemon=True)
                t2 = threading.Thread(target=forward, args=(remote, client), daemon=True)
                t1.start()
                t2.start()
                t1.join()
                t2.join()
                
            except Exception as e:
                client.sendall(f'HTTP/1.1 502 Bad Gateway\r\n\r\n{e}'.encode())
            finally:
                try: remote.close()
                except: pass
        
        else:
            # HTTP forward
            # Parse URL
            from urllib.parse import urlparse
            parsed = urlparse(target)
            host = parsed.hostname
            port = parsed.port or 80
            path = parsed.path or '/'
            if parsed.query:
                path += '?' + parsed.query
            
            try:
                remote = socket.create_connection((host, port), timeout=15)
                # Rewrite request line
                new_req = req.replace(first_line.encode(), f'{method} {path} HTTP/1.1'.encode(), 1)
                # Fix Host header if needed
                remote.sendall(new_req)
                
                while True:
                    data = remote.recv(BUFSIZE)
                    if not data:
                        break
                    client.sendall(data)
            finally:
                try: remote.close()
                except: pass
    
    except Exception as e:
        pass
    finally:
        try: client.close()
        except: pass

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', PORT))
    server.listen(100)
    
    print(f"Прокси запущен на 0.0.0.0:{PORT}")
    print(f"Настрой браузер: HTTP прокси -> {socket.gethostbyname(socket.gethostname())}:{PORT}")
    print(f"Нажми Ctrl+C для остановки")
    
    try:
        while True:
            client, addr = server.accept()
            threading.Thread(target=handle_client, args=(client,), daemon=True).start()
    except KeyboardInterrupt:
        print("\nПрокси остановлен.")
    finally:
        server.close()

if __name__ == '__main__':
    main()
