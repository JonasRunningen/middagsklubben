import os
import uuid
from datetime import date
from flask import (Flask, render_template, request, redirect,
                   url_for, flash, abort)
from werkzeug.utils import secure_filename
from sqlalchemy import func
from models import db, Member, Dinner, Drink, Score, Photo

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
        flash('Ny kveld er registrert! 🍷', 'success')
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
                           photos=dinner.photos.order_by(Photo.uploaded_at).all())


@app.route('/kveld/<int:dinner_id>/score', methods=['POST'])
def add_score(dinner_id):
    dinner    = Dinner.query.get_or_404(dinner_id)
    member_id = int(request.form['member_id'])
    score_val = request.form.get('score', '').strip()
    comment   = request.form.get('comment', '').strip()

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
def medlemmer():
    members = Member.query.order_by(Member.order_index).all()
    return render_template('medlemmer.html', members=members)


@app.route('/medlemmer/legg_til', methods=['POST'])
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
def slett_medlem(member_id):
    member = Member.query.get_or_404(member_id)
    member.active = False
    db.session.commit()
    flash(f'{member.name} er fjernet fra roteringen.', 'success')
    return redirect(url_for('medlemmer'))


@app.route('/medlemmer/<int:member_id>/flytt', methods=['POST'])
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


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app.run(debug=True)
