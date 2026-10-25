Egy okos parkolórendszer fejlesztése, amely lehetővé teszi a szabad parkolóhelyek megtekintését, foglalását.
A projekt jelenleg fejlesztés alatt van. 

A `version1` mappában Flask és Supabase Auth alapú regisztráció és belépés található.
Sikeres belépés után a védett **You are in** oldal nyílik meg.

## Indítás

1. Python 3.11 vagy újabb verzió legyen telepítve.
2. Másold a `version1/.env.example` fájlt `version1/.env` néven.
3. Töltsd ki a Supabase publishable key és a véletlen Flask secret key értékét.
   Flask-kulcs generálása: `python -c "import secrets; print(secrets.token_hex(32))"`.
4. Dupla kattintás: `version1/inditas.bat`.
5. Böngésző: `http://127.0.0.1:5000`.

A `.env` helyi beállítás, nem kerül a Gitbe. A service_role / secret API-kulcs nem szükséges.
A jelenlegi fejlesztői gépen a `.env` már be van állítva.

## Supabase beállítás másik projekthez

- Futtasd a `supabase/migrations/20261019000000_auth_profiles.sql` fájlt az SQL Editorban.
- Az emailes belépés és az email-megerősítés legyen engedélyezve.
- Authentication → URL Configuration → Redirect URLs:
  `http://127.0.0.1:5000/auth/confirmed` és `http://localhost:5000/auth/confirmed`.
- Éles HTTPS tárhelyen állítsd be a saját visszatérési címet és a `COOKIE_SECURE=true` értéket.
  A helyi Flask fejlesztői szerver csak a saját gépen fut; GitHubra feltöltve nem lesz automatikusan tárhely.

## Működés

1. **Create account:** név, email, legalább 8 karakteres jelszó.
2. A Supabase Auth létrehozza a fiókot, és megerősítő emailt küld.
3. Egy adatbázis-trigger a `public.users` táblába másolja a nevet és az emailt.
   A négy mező: `id`, `name`, `email`, `role`. A jelszó csak az Auth rendszerhez kerül.
4. A megerősítő link után visszatérsz a belépőoldalra. A Flasknak eközben futnia kell.
5. **Sign in:** a Supabase ellenőrzi az adatokat, a Flask eltárolja a munkamenet tokenjeit.
6. A `/inside` minden megnyitásakor a Flask ellenőrzi a felhasználót a Supabase-nél.
7. **Sign out:** kijelentkeztet és törli a helyi munkamenetet.

Lejárt vagy elveszett megerősítő link esetén írd be az emailcímet a belépőoldalon,
és kattints a **Resend confirmation email** gombra. Ehhez nem kell kitölteni a jelszót.

Az adatbázis RLS-szabálya szerint a bejelentkezett felhasználó csak a saját profilját olvashatja.
A szerepkört regisztrációkor mindig `user` értékre állítja a trigger.
A sütit a Flask aláírja, HttpOnly és SameSite=Lax beállítással; a jelszót nem tároljuk benne.

A Supabase beépített emailküldője korlátozott teszteléshez való. Ha más osztálytársaknak
nem enged levelet küldeni vagy eléritek a limitet, saját SMTP-szolgáltató szükséges.
Az email-megerősítést ehhez nem kell kikapcsolni.

## Fájlok és ellenőrzés

- `version1/app.py`: Flask útvonalak, Supabase-kapcsolat, munkamenet és űrlapellenőrzés.
- `version1/templates/`: belépés/regisztráció, megerősítési visszatérés, védett oldal.
- `version1/static/style.css`: a meglévő sötétkék–menta megjelenés és mobilnézet.
- `version1/static/confirmed.js`: eltávolítja az emailes visszatérés URL-jéből a tokeneket.
- `supabase/migrations/`: a felhasználói tábla, trigger és hozzáférési szabályok.
- Tesztek a `version1` mappából: `.venv\Scripts\python.exe -m unittest -v test_auth`.

A parkolóhelyek, foglalások és fizetések funkciói ebben a változatban még nincsenek megvalósítva.
