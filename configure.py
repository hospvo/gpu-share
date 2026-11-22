"""Generate one hub configuration and separate credentials for each participant."""
import ipaddress
import json
import secrets
from pathlib import Path


def address(prompt):
    while True:
        try:
            ip = ipaddress.IPv4Address(input(prompt).strip())
            if ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10"):
                return str(ip)
        except ValueError:
            pass
        print("Zadej Tailscale IPv4 (100.64–127.x.x) nebo 127.0.0.1 pro místní test.")


def positive(prompt):
    while True:
        value = input(prompt).strip()
        if value.isdecimal() and int(value) > 0:
            return int(value)


def main():
    root = Path("private")
    if root.exists():
        raise SystemExit("Adresář private již existuje. Uprav stávající konfiguraci nebo spusť průvodce v nové složce.")
    hub = {"bind": address("Tailscale IP koordinátoru: "), "port": 8787, "nodes": [], "users": {}}
    files = {}
    for i in range(positive("Počet počítačů s GPU: ")):
        name = input(f"Název GPU počítače {i+1}: ").strip() or f"gpu-{i+1}"
        ip = address("Tailscale IP tohoto počítače: ")
        agent = {"bind": ip, "port": 8788, "key": secrets.token_urlsafe(32),
                 "capacity_mib": positive("VRAM v MiB (12288 nebo 16384): "),
                 "reserve_mib": 1024, "context_length": 4096, "max_gpu_percent": 80,
                 "adapter": "", "games": ["hoi4.exe", "ck3.exe"], "models": {}}
        for _ in range(positive("Počet sdílených modelů: ")):
            model = input("Přesný identifikátor modelu z LM Studio: ").strip()
            if not model or model.startswith("-"):
                raise SystemExit("Neplatný identifikátor modelu")
            agent["models"][model] = positive("Rozpočet VRAM pro model při kontextu 4096 (MiB): ")
        hub["nodes"].append({"name": name, "url": f"http://{ip}:8788", "key": agent["key"]})
        files[f"agent-{i+1}/agent.json"] = agent
    for i in range(positive("Počet uživatelů API: ")):
        name = input(f"Jméno uživatele {i+1} v oznámeních: ").strip()
        if not name or not name.isascii() or not name.isprintable() or name in hub["users"]:
            raise SystemExit("Použij jedinečné tisknutelné jméno bez diakritiky.")
        key = secrets.token_urlsafe(32)
        hub["users"][name] = key
        files[f"client-{i+1}.json"] = {"name": name, "base_url": f"http://{hub['bind']}:8787/v1", "api_key": key}
    files["hub.json"] = hub
    root.mkdir()
    for path, value in files.items():
        target = root / path
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Hotovo. Každému předej jen jeho konfiguraci z adresáře private.")


if __name__ == "__main__":
    main()

