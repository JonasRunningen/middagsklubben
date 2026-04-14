import os
import uuid
import json
import base64
from datetime import date
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, flash, abort, jsonify, session)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func
from models import db, Member, Dinner, Drink, Score, Photo, Quote, Recipe, Award, HostDebt

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR      = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
ALLOWED_EXT   = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

app = Flask(__name__)
app.config['SECRET_KEY']            = os.environ.get('SECRET_KEY', 'middag-hemmelig-nokkel')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(BASE_DIR, "middagsklubben.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH']    = 16 * 1024 * 1024   # 16 MB

db.init_app(app)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

with app.app_context():
    db.create_all()
    # Migrate: add new columns if missing
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    cols = [c['name'] for c in inspector.get_columns('members')]
    with db.engine.connect() as conn:
        if 'is_admin' not in cols:
            conn.execute(text('ALTER TABLE members ADD COLUMN is_admin BOOLEAN DEFAULT 0'))
        if 'username' not in cols:
            conn.execute(text('ALTER TABLE members ADD COLUMN username VARCHAR(100)'))
        if 'password_hash' not in cols:
            conn.execute(text('ALTER TABLE members ADD COLUMN password_hash VARCHAR(200)'))
        conn.commit()

    # Ensure default admin user exists
    admin = Member.query.filter_by(username='admin').first()
    if not admin:
        admin = Member(
            name='Admin',
            order_index=0,
            active=False,   # not in dinner rotation
            is_admin=True,
            username='admin',
            password_hash=generate_password_hash('admin', method='pbkdf2:sha256'),
        )
        db.session.add(admin)
        db.session.commit()
    elif not admin.password_hash:
        admin.password_hash = generate_password_hash('admin', method='pbkdf2:sha256')
        db.session.commit()


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        member = Member.query.filter(
            db.func.lower(Member.username) == username
        ).first()
        if member and member.password_hash and check_password_hash(member.password_hash, password):
            session['logged_in']   = True
            session['member_id']   = member.id
            session['member_name'] = member.name
            session['is_admin']    = member.is_admin
            session.permanent      = True
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        flash('Feil brukernavn eller passord.', 'error')
    return render_template('login.html')


@app.route('/logg_ut')
def logg_ut():
    session.clear()
    return redirect(url_for('login'))


@app.before_request
def require_login():
    public = {'login', 'static'}
    if request.endpoint not in public and not session.get('logged_in'):
        return redirect(url_for('login', next=request.path))


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Du må være administrator for å se denne siden.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


# ── Helpers ───────────────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


def next_host():
    """Return the Member who should host next, based on rotation order."""
    members = Member.query.filter_by(active=True).order_by(Member.order_index).all()
    if not members:
        return None
    last = Dinner.query.order_by(Dinner.date.desc()).first()
    if not last or last.host_id is None:
        return members[0]
    ids = [m.id for m in members]
    try:
        idx = ids.index(last.host_id)
        return members[(idx + 1) % len(members)]
    except ValueError:
        return members[0]


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    dinners = Dinner.query.order_by(Dinner.date.desc()).all()
    suggestion = next_host()
    members    = Member.query.filter_by(active=True).order_by(Member.order_index).all()
    return render_template('index.html',
                           dinners=dinners,
                           suggestion=suggestion,
                           members=members,
                           today=date.today())


# ── New dinner ────────────────────────────────────────────────────────────────

@app.route('/ny_kveld', methods=['GET', 'POST'])
def ny_kveld():
    members    = Member.query.filter_by(active=True).order_by(Member.order_index).all()
    suggestion = next_host()

    if request.method == 'POST':
        f = request.form

        # Validate date
        try:
            dinner_date = date.fromisoformat(f['date'])
        except (KeyError, ValueError):
            flash('Ugyldig dato.', 'error')
            return redirect(url_for('ny_kveld'))

        host_id = f.get('host_id') or None
        if host_id:
            host_id = int(host_id)

        dinner = Dinner(
            date            = dinner_date,
            host_id         = host_id,
            forrett_cook_id = int(f['forrett_cook_id']) if f.get('forrett_cook_id') else None,
            dessert_cook_id = int(f['dessert_cook_id']) if f.get('dessert_cook_id') else None,
            forrett         = f.get('forrett', '').strip(),
            hovedrett       = f.get('hovedrett', '').strip(),
            dessert         = f.get('dessert', '').strip(),
            category        = f.get('category', '').strip(),
            notes           = f.get('notes', '').strip(),
        )
        db.session.add(dinner)
        db.session.flush()   # get dinner.id before drink inserts

        # Drinks (repeated fields: drink_type[], drink_name[], …)
        types  = request.form.getlist('drink_type[]')
        names  = request.form.getlist('drink_name[]')
        years  = request.form.getlist('drink_year[]')
        grapes = request.form.getlist('drink_grape[]')
        notes_list = request.form.getlist('drink_notes[]')

        for i, name in enumerate(names):
            if not name.strip():
                continue
            drink = Drink(
                dinner_id     = dinner.id,
                type          = types[i]  if i < len(types)  else 'vin',
                name          = name.strip(),
                year          = int(years[i]) if i < len(years) and years[i].strip().isdigit() else None,
                grape_type    = grapes[i].strip()     if i < len(grapes)     else '',
                tasting_notes = notes_list[i].strip() if i < len(notes_list) else '',
            )
            db.session.add(drink)

        db.session.commit()
        flash('Ny kveld er registrert! 🍴', 'success')
        return redirect(url_for('kveld', dinner_id=dinner.id))

    return render_template('ny_kveld.html', members=members, suggestion=suggestion,
                           today=date.today().isoformat())


# ── Dinner detail ─────────────────────────────────────────────────────────────

@app.route('/kveld/<int:dinner_id>')
def kveld(dinner_id):
    dinner  = Dinner.query.get_or_404(dinner_id)
    members = Member.query.filter_by(active=True).order_by(Member.order_index).all()

    # Build score map: member_id → Score object
    score_map = {s.member_id: s for s in dinner.scores}

    return render_template('kveld.html',
                           dinner=dinner,
                           members=members,
                           score_map=score_map,
                           drinks=dinner.drinks.all(),
                           photos=dinner.photos.order_by(Photo.uploaded_at).all(),
                           quotes=dinner.quotes.order_by(Quote.added_at).all(),
                           recipes=dinner.recipes.all())


@app.route('/kveld/<int:dinner_id>/score', methods=['POST'])
def add_score(dinner_id):
    dinner    = Dinner.query.get_or_404(dinner_id)
    member_id = int(request.form['member_id'])
    score_val = request.form.get('score', '').strip()
    comment   = request.form.get('comment', '').strip()

    # Block self-voting
    if dinner.host_id and member_id == dinner.host_id:
        flash('Du kan ikke stemme på din egen middag.', 'error')
        return redirect(url_for('kveld', dinner_id=dinner_id))

    existing = Score.query.filter_by(dinner_id=dinner_id, member_id=member_id).first()
    if existing:
        existing.score   = int(score_val) if score_val else None
        existing.comment = comment
    else:
        s = Score(dinner_id=dinner_id, member_id=member_id,
                  score=int(score_val) if score_val else None,
                  comment=comment)
        db.session.add(s)

    db.session.commit()
    flash('Poengsum lagret.', 'success')
    return redirect(url_for('kveld', dinner_id=dinner_id))


@app.route('/kveld/<int:dinner_id>/foto', methods=['POST'])
def upload_foto(dinner_id):
    Dinner.query.get_or_404(dinner_id)
    files = request.files.getlist('photos')

    if not files or all(f.filename == '' for f in files):
        flash('Ingen bilder valgt.', 'error')
        return redirect(url_for('kveld', dinner_id=dinner_id))

    for f in files:
        if f and allowed_file(f.filename):
            ext      = secure_filename(f.filename).rsplit('.', 1)[1].lower()
            filename = f'{uuid.uuid4().hex}.{ext}'
            f.save(os.path.join(UPLOAD_FOLDER, filename))
            db.session.add(Photo(dinner_id=dinner_id, filename=filename))

    db.session.commit()
    flash('Bilde(r) lastet opp!', 'success')
    return redirect(url_for('kveld', dinner_id=dinner_id))


@app.route('/kveld/<int:dinner_id>/slett_foto/<int:photo_id>', methods=['POST'])
def slett_foto(dinner_id, photo_id):
    photo = Photo.query.get_or_404(photo_id)
    if photo.dinner_id != dinner_id:
        abort(403)
    filepath = os.path.join(UPLOAD_FOLDER, photo.filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    db.session.delete(photo)
    db.session.commit()
    flash('Bilde slettet.', 'success')
    return redirect(url_for('kveld', dinner_id=dinner_id))


@app.route('/kveld/<int:dinner_id>/slett', methods=['POST'])
def slett_kveld(dinner_id):
    dinner = Dinner.query.get_or_404(dinner_id)
    # Remove photo files
    for photo in dinner.photos:
        filepath = os.path.join(UPLOAD_FOLDER, photo.filename)
        if os.path.exists(filepath):
            os.remove(filepath)
    db.session.delete(dinner)
    db.session.commit()
    flash('Kvelden er slettet.', 'success')
    return redirect(url_for('index'))


# ── Statistics ────────────────────────────────────────────────────────────────

@app.route('/statistikk')
def statistikk():
    total_dinners = Dinner.query.count()
    total_drinks  = Drink.query.count()

    # Dinners per member (as host)
    dinners_per_member = (
        db.session.query(Member.name, func.count(Dinner.id).label('count'))
        .outerjoin(Dinner, Dinner.host_id == Member.id)
        .filter(Member.active == True)
        .group_by(Member.id)
        .order_by(func.count(Dinner.id).desc())
        .all()
    )

    # Average score per member (as scorer)
    avg_score_per_member = (
        db.session.query(Member.name, func.avg(Score.score).label('avg'))
        .join(Score, Score.member_id == Member.id)
        .filter(Member.active == True)
        .group_by(Member.id)
        .order_by(func.avg(Score.score).desc())
        .all()
    )
    avg_score_per_member = [
        (name, round(avg, 2)) for name, avg in avg_score_per_member if avg
    ]

    # Best dinner (highest avg score)
    best_dinner = None
    best_score  = 0
    for dinner in Dinner.query.all():
        if dinner.avg_score and dinner.avg_score > best_score:
            best_score  = dinner.avg_score
            best_dinner = dinner

    # All dinners for timeline
    dinners = Dinner.query.order_by(Dinner.date.desc()).limit(5).all()

    return render_template('statistikk.html',
                           total_dinners=total_dinners,
                           total_drinks=total_drinks,
                           dinners_per_member=dinners_per_member,
                           avg_score_per_member=avg_score_per_member,
                           best_dinner=best_dinner,
                           best_score=best_score,
                           recent_dinners=dinners)


# ── Members ───────────────────────────────────────────────────────────────────

@app.route('/medlemmer')
@admin_required
def medlemmer():
    members = Member.query.order_by(Member.order_index).all()
    return render_template('medlemmer.html', members=members)


@app.route('/medlemmer/legg_til', methods=['POST'])
@admin_required
def legg_til_medlem():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Navn kan ikke være tomt.', 'error')
        return redirect(url_for('medlemmer'))
    max_order = db.session.query(func.max(Member.order_index)).scalar() or 0
    member = Member(name=name, order_index=max_order + 1)
    db.session.add(member)
    db.session.commit()
    flash(f'{name} er lagt til! 🎉', 'success')
    return redirect(url_for('medlemmer'))


@app.route('/medlemmer/<int:member_id>/slett', methods=['POST'])
@admin_required
def slett_medlem(member_id):
    member = Member.query.get_or_404(member_id)
    member.active = False
    db.session.commit()
    flash(f'{member.name} er fjernet fra roteringen.', 'success')
    return redirect(url_for('medlemmer'))


@app.route('/medlemmer/<int:member_id>/flytt', methods=['POST'])
@admin_required
def flytt_medlem(member_id):
    direction = request.form.get('direction')
    members   = Member.query.filter_by(active=True).order_by(Member.order_index).all()
    ids       = [m.id for m in members]
    if member_id not in ids:
        abort(404)
    idx = ids.index(member_id)
    if direction == 'opp' and idx > 0:
        members[idx].order_index, members[idx-1].order_index = \
            members[idx-1].order_index, members[idx].order_index
    elif direction == 'ned' and idx < len(members) - 1:
        members[idx].order_index, members[idx+1].order_index = \
            members[idx+1].order_index, members[idx].order_index
    db.session.commit()
    return redirect(url_for('medlemmer'))


@app.route('/medlemmer/<int:member_id>/toggle_admin', methods=['POST'])
@admin_required
def toggle_admin(member_id):
    member = Member.query.get_or_404(member_id)
    # Prevent removing own admin status
    if member.id == session.get('member_id') and member.is_admin:
        flash('Du kan ikke fjerne din egen adminstatus.', 'error')
        return redirect(url_for('medlemmer'))
    member.is_admin = not member.is_admin
    db.session.commit()
    status = 'administrator' if member.is_admin else 'vanlig bruker'
    flash(f'{member.name} er nå {status}.', 'success')
    return redirect(url_for('medlemmer'))


# ── User management (admin only) ─────────────────────────────────────────────

@app.route('/medlemmer/<int:member_id>/sett_bruker', methods=['POST'])
@admin_required
def sett_bruker(member_id):
    member   = Member.query.get_or_404(member_id)
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()

    if not username:
        flash('Brukernavn kan ikke være tomt.', 'error')
        return redirect(url_for('medlemmer'))

    # Check uniqueness
    existing = Member.query.filter(
        db.func.lower(Member.username) == username.lower(),
        Member.id != member_id
    ).first()
    if existing:
        flash(f'Brukernavnet "{username}" er allerede i bruk.', 'error')
        return redirect(url_for('medlemmer'))

    member.username = username
    if password:
        member.password_hash = generate_password_hash(password)
    db.session.commit()
    flash(f'Bruker for {member.name} er oppdatert.', 'success')
    return redirect(url_for('medlemmer'))


# ── Scoreboard ────────────────────────────────────────────────────────────────

@app.route('/scoreboard')
def scoreboard():
    members = Member.query.filter_by(active=True).order_by(Member.name).all()

    scores = []
    for m in members:
        # Total score = sum of all scores given to dinners hosted by this member
        # Excluding self-votes (scorer != host)
        total = db.session.query(func.sum(Score.score)).join(
            Dinner, Score.dinner_id == Dinner.id
        ).filter(
            Dinner.host_id == m.id,
            Score.member_id != m.id,
            Score.score.isnot(None)
        ).scalar() or 0

        vote_count = db.session.query(func.count(Score.id)).join(
            Dinner, Score.dinner_id == Dinner.id
        ).filter(
            Dinner.host_id == m.id,
            Score.member_id != m.id,
            Score.score.isnot(None)
        ).scalar() or 0

        avg = round(total / vote_count, 1) if vote_count else 0
        hosted = m.dinners_hosted.count()
        scores.append({
            'member': m,
            'total': total,
            'avg': avg,
            'votes': vote_count,
            'hosted': hosted,
        })

    scores.sort(key=lambda x: (-x['total'], -x['avg'], x['member'].name))
    return render_template('scoreboard.html', scores=scores)


# ── Min side ─────────────────────────────────────────────────────────────────

@app.route('/min-side')
def min_side():
    member_id = session.get('member_id')
    member = Member.query.get_or_404(member_id)

    # --- Scoreboard rank ---
    all_members = Member.query.filter_by(active=True).all()
    all_scores = []
    for m in all_members:
        total = db.session.query(func.sum(Score.score)).join(
            Dinner, Score.dinner_id == Dinner.id
        ).filter(
            Dinner.host_id == m.id,
            Score.member_id != m.id,
            Score.score.isnot(None)
        ).scalar() or 0
        all_scores.append((m.id, total))
    all_scores.sort(key=lambda x: -x[1])
    rank = next((i + 1 for i, (mid, _) in enumerate(all_scores) if mid == member_id), None)
    my_total_stars = next((t for mid, t in all_scores if mid == member_id), 0)

    # --- Stars received (avg from others on my hosted dinners) ---
    my_hosted_dinners = Dinner.query.filter_by(host_id=member_id).all()
    hosted_ids = [d.id for d in my_hosted_dinners]

    received_scores = db.session.query(Score.score).filter(
        Score.dinner_id.in_(hosted_ids),
        Score.member_id != member_id,
        Score.score.isnot(None)
    ).all() if hosted_ids else []
    received_vals = [s[0] for s in received_scores]
    avg_received = round(sum(received_vals) / len(received_vals), 1) if received_vals else None

    # --- Scores I have given (avg) ---
    given_scores = db.session.query(Score.score).filter(
        Score.member_id == member_id,
        Score.score.isnot(None)
    ).all()
    given_vals = [s[0] for s in given_scores]
    avg_given = round(sum(given_vals) / len(given_vals), 1) if given_vals else None

    # --- Strictness label ---
    strictness = None
    if avg_given is not None:
        if avg_given >= 8.5:
            strictness = ('Mild dommer', '😇')
        elif avg_given >= 7:
            strictness = ('Rettferdig dommer', '⚖️')
        elif avg_given >= 5.5:
            strictness = ('Streng dommer', '🧐')
        else:
            strictness = ('Knallhard dommer', '😤')

    # --- Participation ---
    all_dinners = Dinner.query.order_by(Dinner.date.desc()).all()
    participated = []
    for d in all_dinners:
        scored = Score.query.filter_by(dinner_id=d.id, member_id=member_id).first()
        if scored or d.host_id == member_id:
            participated.append(d)

    # --- My hosted dinners with their avg score ---
    hosted_with_scores = []
    for d in my_hosted_dinners:
        hosted_with_scores.append({'dinner': d, 'avg': d.avg_score})
    hosted_with_scores.sort(key=lambda x: x['dinner'].date, reverse=True)

    # --- My given scores with comments ---
    my_scores = db.session.query(Score, Dinner).join(
        Dinner, Score.dinner_id == Dinner.id
    ).filter(
        Score.member_id == member_id,
        Score.score.isnot(None)
    ).order_by(Dinner.date.desc()).all()

    # --- Debt ---
    my_debts = HostDebt.query.filter_by(debtor_id=member_id, settled=False).all()
    my_credits = HostDebt.query.filter_by(creditor_id=member_id, settled=False).all()

    return render_template('min_side.html',
        member=member,
        rank=rank,
        total_members=len(all_scores),
        my_total_stars=my_total_stars,
        avg_received=avg_received,
        avg_given=avg_given,
        strictness=strictness,
        hosted_count=len(my_hosted_dinners),
        participated_count=len(participated),
        hosted_dinners=hosted_with_scores,
        my_scores=my_scores,
        my_debts=my_debts,
        my_credits=my_credits,
    )


@app.route('/min-side/bytt-passord', methods=['POST'])
def bytt_passord():
    member_id = session.get('member_id')
    member = Member.query.get_or_404(member_id)

    current  = request.form.get('current_password', '')
    new_pw   = request.form.get('new_password', '').strip()
    confirm  = request.form.get('confirm_password', '').strip()

    if not member.password_hash or not check_password_hash(member.password_hash, current):
        flash('Nåværende passord er feil.', 'error')
        return redirect(url_for('min_side'))
    if len(new_pw) < 4:
        flash('Passord må være minst 4 tegn.', 'error')
        return redirect(url_for('min_side'))
    if new_pw != confirm:
        flash('Passordene stemmer ikke overens.', 'error')
        return redirect(url_for('min_side'))

    member.password_hash = generate_password_hash(new_pw, method='pbkdf2:sha256')
    db.session.commit()
    flash('Passord oppdatert! 🎉', 'success')
    return redirect(url_for('min_side'))


# ── Quotes ────────────────────────────────────────────────────────────────────

@app.route('/kveld/<int:dinner_id>/sitat', methods=['POST'])
def legg_til_sitat(dinner_id):
    Dinner.query.get_or_404(dinner_id)
    text      = request.form.get('text', '').strip()
    member_id = request.form.get('member_id') or None
    if text:
        q = Quote(dinner_id=dinner_id, text=text,
                  member_id=int(member_id) if member_id else None)
        db.session.add(q)
        db.session.commit()
    return redirect(url_for('kveld', dinner_id=dinner_id))


@app.route('/kveld/<int:dinner_id>/sitat/<int:quote_id>/slett', methods=['POST'])
def slett_sitat(dinner_id, quote_id):
    q = Quote.query.get_or_404(quote_id)
    db.session.delete(q)
    db.session.commit()
    return redirect(url_for('kveld', dinner_id=dinner_id))


# ── Recipes ───────────────────────────────────────────────────────────────────

COURSE_LABELS = {'forrett': 'Forrett', 'hoved': 'Hovedrett', 'dessert': 'Dessert'}


@app.route('/kveld/<int:dinner_id>/oppskrift/<course>', methods=['GET', 'POST'])
def oppskrift(dinner_id, course):
    if course not in COURSE_LABELS:
        abort(404)
    dinner = Dinner.query.get_or_404(dinner_id)
    recipe = Recipe.query.filter_by(dinner_id=dinner_id, course=course).first()

    if request.method == 'POST':
        f = request.form
        if recipe is None:
            recipe = Recipe(dinner_id=dinner_id, course=course)
            db.session.add(recipe)
        recipe.title        = f.get('title', '').strip()
        recipe.ingredients  = f.get('ingredients', '').strip()
        recipe.instructions = f.get('instructions', '').strip()
        recipe.source_url   = f.get('source_url', '').strip()
        db.session.commit()
        flash('Oppskrift lagret! 📖', 'success')
        return redirect(url_for('oppskrift', dinner_id=dinner_id, course=course))

    return render_template('oppskrift.html', dinner=dinner, recipe=recipe,
                           course=course, course_label=COURSE_LABELS[course])


@app.route('/kveld/<int:dinner_id>/handleliste')
def handleliste(dinner_id):
    dinner  = Dinner.query.get_or_404(dinner_id)
    recipes = Recipe.query.filter_by(dinner_id=dinner_id).all()
    items   = []
    for r in recipes:
        if r.ingredients:
            for line in r.ingredients.splitlines():
                line = line.strip()
                if line:
                    items.append({'course': COURSE_LABELS.get(r.course, r.course),
                                  'text': line, 'recipe': r.title})
    return render_template('handleliste.html', dinner=dinner, items=items, recipes=recipes)


# ── Awards ────────────────────────────────────────────────────────────────────

AWARD_CATEGORIES = [
    ('beste_forrett',  '🥗 Beste forrett'),
    ('beste_hoved',    '🍖 Beste hovedrett'),
    ('beste_dessert',  '🍮 Beste dessert'),
    ('beste_vin',      '🍷 Beste vinvalg'),
    ('kveldets_mvp',   '🌟 Kveldens MVP'),
    ('arets_vert',     '🏆 Årets vert'),
    ('overraskelse',   '😮 Mest overraskende rett'),
    ('morsomste',      '😂 Morsomste øyeblikk'),
]
AWARD_DICT = dict(AWARD_CATEGORIES)


@app.route('/awards')
@app.route('/awards/<int:year>')
def awards(year=None):
    from datetime import date as dt
    all_years = db.session.query(db.func.distinct(Award.year)).order_by(Award.year.desc()).all()
    all_years = [y[0] for y in all_years]
    if year is None:
        year = dt.today().year
    if year not in all_years and all_years:
        year = all_years[0]
    awards_this_year = Award.query.filter_by(year=year).order_by(Award.created_at).all()
    dinners = Dinner.query.order_by(Dinner.date.desc()).all()
    return render_template('awards.html',
                           awards=awards_this_year,
                           all_years=all_years,
                           year=year,
                           categories=AWARD_CATEGORIES,
                           award_dict=AWARD_DICT,
                           dinners=dinners)


@app.route('/awards/legg_til', methods=['POST'])
def legg_til_award():
    from datetime import date as dt
    f        = request.form
    year     = int(f.get('year', dt.today().year))
    category = f.get('category', '').strip()
    winner   = f.get('winner', '').strip()
    desc     = f.get('description', '').strip()
    dinner_id = f.get('dinner_id') or None
    if category and winner:
        a = Award(year=year, category=category, winner=winner,
                  description=desc,
                  dinner_id=int(dinner_id) if dinner_id else None)
        db.session.add(a)
        db.session.commit()
        flash('Award delt ut! 🏆', 'success')
    return redirect(url_for('awards', year=year))


@app.route('/awards/<int:award_id>/slett', methods=['POST'])
def slett_award(award_id):
    a = Award.query.get_or_404(award_id)
    year = a.year
    db.session.delete(a)
    db.session.commit()
    return redirect(url_for('awards', year=year))


# ── Gjeld ─────────────────────────────────────────────────────────────────────

@app.route('/gjeld')
def gjeld():
    members = Member.query.filter_by(active=True).order_by(Member.order_index).all()
    debts   = HostDebt.query.order_by(HostDebt.settled, HostDebt.created_at.desc()).all()
    return render_template('gjeld.html', members=members, debts=debts)


@app.route('/gjeld/legg_til', methods=['POST'])
def legg_til_gjeld():
    f           = request.form
    debtor_id   = f.get('debtor_id')
    creditor_id = f.get('creditor_id')
    note        = f.get('note', '').strip()
    if debtor_id and creditor_id and debtor_id != creditor_id:
        d = HostDebt(debtor_id=int(debtor_id), creditor_id=int(creditor_id), note=note)
        db.session.add(d)
        db.session.commit()
        flash('Gjeld registrert.', 'success')
    else:
        flash('Velg to forskjellige personer.', 'error')
    return redirect(url_for('gjeld'))


@app.route('/gjeld/<int:debt_id>/gjor_opp', methods=['POST'])
def gjor_opp_gjeld(debt_id):
    d = HostDebt.query.get_or_404(debt_id)
    d.settled = True
    db.session.commit()
    flash('Gjeld gjort opp! 🎉', 'success')
    return redirect(url_for('gjeld'))


@app.route('/gjeld/<int:debt_id>/slett', methods=['POST'])
def slett_gjeld(debt_id):
    d = HostDebt.query.get_or_404(debt_id)
    db.session.delete(d)
    db.session.commit()
    return redirect(url_for('gjeld'))


# ── Årsoppsummering ───────────────────────────────────────────────────────────

@app.route('/arsoppsummering')
@app.route('/arsoppsummering/<int:year>')
def arsoppsummering(year=None):
    from datetime import date as dt
    from collections import Counter

    all_years = db.session.query(
        db.func.strftime('%Y', Dinner.date).label('yr')
    ).distinct().order_by('yr').all()
    all_years = [int(y[0]) for y in all_years]

    if year is None:
        year = dt.today().year

    dinners = Dinner.query.filter(
        db.func.strftime('%Y', Dinner.date) == str(year)
    ).order_by(Dinner.date).all()

    total_dinners = len(dinners)
    dinner_ids    = [d.id for d in dinners]

    total_drinks  = Drink.query.filter(Drink.dinner_id.in_(dinner_ids)).count() if dinner_ids else 0
    total_quotes  = Quote.query.filter(Quote.dinner_id.in_(dinner_ids)).count() if dinner_ids else 0

    categories = [d.category for d in dinners if d.category]
    top_cat    = Counter(categories).most_common(1)[0] if categories else None

    best_dinner = None
    best_score  = 0
    for d in dinners:
        if d.avg_score and d.avg_score > best_score:
            best_score  = d.avg_score
            best_dinner = d

    host_counts = Counter(d.host.name for d in dinners if d.host)
    top_host    = host_counts.most_common(1)[0] if host_counts else None

    awards_year = Award.query.filter_by(year=year).all()
    award_dict  = AWARD_DICT

    recent_quotes = (Quote.query
                     .filter(Quote.dinner_id.in_(dinner_ids))
                     .order_by(Quote.added_at.desc())
                     .limit(5).all()) if dinner_ids else []

    all_scores = db.session.query(Score.score).filter(
        Score.dinner_id.in_(dinner_ids), Score.score.isnot(None)
    ).all() if dinner_ids else []
    avg_score_year = round(sum(s[0] for s in all_scores) / len(all_scores), 1) if all_scores else None

    return render_template('arsoppsummering.html',
                           year=year,
                           all_years=all_years,
                           dinners=dinners,
                           total_dinners=total_dinners,
                           total_drinks=total_drinks,
                           total_quotes=total_quotes,
                           top_cat=top_cat,
                           best_dinner=best_dinner,
                           best_score=best_score,
                           top_host=top_host,
                           awards_year=awards_year,
                           award_dict=award_dict,
                           recent_quotes=recent_quotes,
                           avg_score_year=avg_score_year,
                           unique_cats=len(set(categories)))


# ── Wine label scan ───────────────────────────────────────────────────────────

@app.route('/api/skann_etikett', methods=['POST'])
def skann_etikett():
    import anthropic

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY er ikke satt på serveren'}), 500

    file = request.files.get('image')
    if not file:
        return jsonify({'error': 'Ingen bilde lastet opp'}), 400

    raw = file.read()
    image_data = base64.standard_b64encode(raw).decode('utf-8')
    media_type = file.content_type or 'image/jpeg'
    if media_type not in ('image/jpeg', 'image/png', 'image/gif', 'image/webp'):
        media_type = 'image/jpeg'

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model='claude-opus-4-6',
        max_tokens=512,
        messages=[{
            'role': 'user',
            'content': [
                {
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': media_type,
                        'data': image_data,
                    }
                },
                {
                    'type': 'text',
                    'text': (
                        'Analyser dette vin/øl-etikettbildet og returner informasjon i JSON-format med disse feltene:\n'
                        '- type: "vin", "øl" eller "annet"\n'
                        '- name: fullt produktnavn inkludert produsent\n'
                        '- year: årgangstall som heltall, eller null hvis ikke synlig\n'
                        '- grape_type: druetype (vin) eller ølstil (øl), tom streng hvis ukjent\n'
                        '- tasting_notes: smaksnotater fra etiketten, eller en kort norsk beskrivelse basert på vintypen\n\n'
                        'Svar KUN med gyldig JSON, ingen annen tekst.'
                    )
                }
            ]
        }]
    )

    text = message.content[0].text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return jsonify({'error': 'Kunne ikke tolke svaret fra AI'}), 500

    return jsonify(data)


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app.run(debug=True)
