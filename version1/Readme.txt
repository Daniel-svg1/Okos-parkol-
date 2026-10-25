Smart Parking - Supabase regisztracio es belepes

Inditas Windows alatt: dupla kattintas az inditas.bat fajlra.
A bongeszoben: http://127.0.0.1:5000

Elso beallitas: a .env.example alapjan hozz letre egy .env fajlt.
Ezen a fejlesztoi gepen ez mar elkeszult. A .env nem kerul a GitHubra.
Create account -> email megerositese -> Sign in -> You are in.
A jelszot a Supabase Auth kezeli; a public.users tablaba a profil kerul.

app.py: a Flask kezeli az oldalakat, urlapokat es a Supabase Auth hivast.
templates/index.html: az oldal tartalma es szerkezete.
static/style.css: szinek, elrendezes, mobilnezet.
static/confirmed.js: emailes visszatereskor eltavolitja a tokeneket az URL-bol.
requirements.txt: Flask, Supabase Python kliens, python-dotenv.
test_auth.py: a belepes es a vedett oldal automatikus tesztjei.

Reszletes leiras: a projekt gyokereben levo README.md.
