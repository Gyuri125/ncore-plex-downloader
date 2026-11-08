"""
Plex Hitelesítési Token Automatikus Lekérő és Beállító Szkript.
A python-plexapi hivatalos eszközei és a helyi rendszer-azonosítás alapján.

Használat:
    python scripts/get_plex_token.py
"""

import sys
import getpass
from pathlib import Path

# Projekt gyökér hozzáadása a PYTHONPATH-hoz
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.services.plex_service import PlexService
from src.config.settings import save_settings, get_settings


def main():
    print("=" * 60)
    print("🎬 Plex Hitelesítési Token Automatikus Lekérése")
    print("=" * 60)

    # 1. Lépés: Helyi token keresése a gépen (Windows Registry / Preferences)
    print("\n🔍 1. Helyi Plex Media Server token keresése a számítógépen...")
    local_token = PlexService.extract_local_token()
    if local_token:
        masked = local_token[:4] + "*" * (len(local_token) - 8) + local_token[-4:]
        print(f"✅ SIKER! Találtam egy helyi Plex tokent ezen a gépen: {masked}")
        ans = input("👉 Elmentsem ezt a tokent a .env fájlba? (I/n): ").strip().lower()
        if ans in ("", "i", "y", "igen", "yes"):
            save_settings({"PLEX_TOKEN": local_token})
            print("🎉 A Plex token sikeresen elmentve a .env fájlba!")
            return

    # 2. Lépés: Plex Fiók (MyPlexAccount) vagy PIN opciók
    print("\n🌐 Válassz az alábbi lehetőségek közül:")
    print("  [1] Bejelentkezés Plex fiókkal (Email/Felhasználónév + Jelszó)")
    print("  [2] Böngészős jóváhagyás (Plex PIN / OAuth)")
    print("  [3] Kilépés")

    choice = input("\nVálasztás (1/2/3): ").strip()

    if choice == "1":
        print("\n--- Plex Fiók Hitelesítés ---")
        username = input("Plex Felhasználónév vagy Email: ").strip()
        if not username:
            print("❌ Nem adtál meg felhasználónevet.")
            return
        password = getpass.getpass("Plex Jelszó: ")
        code = input("Kétlépcsős 2FA kód (ha nincs, üsd le az Entert): ").strip() or None

        print("\n⏳ Kapcsolódás a plex.tv szerverhez...")
        res = PlexService.fetch_token_from_plex_account(username, password, code)
        if not res.get("success"):
            print(f"❌ Hiba: {res.get('message')}")
            return

        token = res["token"]
        masked = token[:4] + "*" * (len(token) - 8) + token[-4:]
        print(f"✅ Sikeres bejelentkezés! Fiók: {res['username']} ({res.get('email', '')})")
        print(f"🔑 Megszerzett Token: {masked}")

        servers = res.get("servers", [])
        selected_url = None
        if servers:
            print(f"\n📺 Talált Plex szerverek a fiókodban ({len(servers)} db):")
            for idx, s in enumerate(servers, start=1):
                print(f"  [{idx}] {s['name']} -> {s['uri']}")
            s_choice = input(f"Válassz szervert [1-{len(servers)}] (Enter = első szerver): ").strip()
            try:
                s_idx = int(s_choice) - 1 if s_choice else 0
                selected_url = servers[s_idx]["uri"]
            except Exception:
                selected_url = servers[0]["uri"]

        updates = {"PLEX_TOKEN": token}
        if selected_url:
            updates["PLEX_SERVER_URL"] = selected_url

        save_settings(updates)
        print("🎉 A beállítások sikeresen mentve a .env fájlba!")

    elif choice == "2":
        print("\n--- Plex Böngészős (PIN / OAuth) Hitelesítés ---")
        pin_res = PlexService.start_pin_login()
        if not pin_res.get("success"):
            print(f"❌ Hiba: {pin_res.get('message')}")
            return

        oauth_url = pin_res["oauth_url"]
        pin_login = pin_res["pin_login"]
        print(f"\n👉 Nyisd meg az alábbi linket a böngésződben:")
        print(f"🔗 {oauth_url}")

        try:
            import webbrowser
            webbrowser.open(oauth_url)
            print("🌐 A böngésző automatikusan megnyílt a bejelentkezési oldallal.")
        except Exception:
            pass

        print("\n⏳ Várakozás a böngészős jóváhagyásra (nyomj Entert miután rákattintottál a Bejelentkezésre a böngészőben)...")
        input("Nyomj Entert az ellenőrzéshez...")

        token = PlexService.check_pin_login(pin_login)
        if token:
            masked = token[:4] + "*" * (len(token) - 8) + token[-4:]
            print(f"✅ Sikeres jóváhagyás! Token: {masked}")
            save_settings({"PLEX_TOKEN": token})
            print("🎉 A Plex token elmentve a .env fájlba!")
        else:
            print("⚠️ A jóváhagyás még nem történt meg, vagy lejárt a kérés. Próbáld újra!")

    else:
        print("Kilépés.")


if __name__ == "__main__":
    main()

