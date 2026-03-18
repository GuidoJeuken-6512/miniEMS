#!/bin/bash
# Port-Forwarding Script für WSL2
# Führt Port 8080 von WSL2 zu Windows weiter

WSL_IP=$(hostname -I | awk '{print $1}')
WINDOWS_IP=$(cat /etc/resolv.conf | grep nameserver | awk '{print $2}')

echo "WSL2 IP: $WSL_IP"
echo "Windows Gateway IP: $WINDOWS_IP"
echo ""
echo "Um Port-Forwarding einzurichten, führe diesen Befehl in PowerShell (als Administrator) aus:"
echo ""
echo "netsh interface portproxy add v4tov4 listenport=8080 listenaddress=0.0.0.0 connectport=8080 connectaddress=$WSL_IP"
echo ""
echo "Oder verwende die WSL2-IP direkt im Browser:"
echo "http://$WSL_IP:8080"


