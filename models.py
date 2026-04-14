from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Member(db.Model):
    __tablename__ = 'members'
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(100), nullable=False)
    order_index  = db.Column(db.Integer, default=0)
    active       = db.Column(db.Boolean, default=True)

    dinners_hosted = db.relationship('Dinner', foreign_keys='Dinner.host_id',
                                     backref='host', lazy='dynamic')
    scores         = db.relationship('Score', backref='member', lazy='dynamic')


class Dinner(db.Model):
    __tablename__ = 'dinners'
    id               = db.Column(db.Integer, primary_key=True)
    date             = db.Column(db.Date, nullable=False)
    host_id          = db.Column(db.Integer, db.ForeignKey('members.id'))
    forrett_cook_id  = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=True)
    dessert_cook_id  = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=True)
    forrett          = db.Column(db.String(300))
    hovedrett        = db.Column(db.String(300))
    dessert          = db.Column(db.String(300))
    category         = db.Column(db.String(100))
    notes            = db.Column(db.Text)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    forrett_cook = db.relationship('Member', foreign_keys=[forrett_cook_id])
    dessert_cook = db.relationship('Member', foreign_keys=[dessert_cook_id])
    drinks   = db.relationship('Drink',  backref='dinner', lazy='dynamic',
                               cascade='all, delete-orphan')
    scores   = db.relationship('Score',  backref='dinner', lazy='dynamic',
                               cascade='all, delete-orphan')
    photos   = db.relationship('Photo',  backref='dinner', lazy='dynamic',
                               cascade='all, delete-orphan')
    quotes   = db.relationship('Quote',  backref='dinner', lazy='dynamic',
                               cascade='all, delete-orphan')
    recipes  = db.relationship('Recipe', backref='dinner', lazy='dynamic',
                               cascade='all, delete-orphan')

    @property
    def avg_score(self):
        vals = [s.score for s in self.scores if s.score is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    @property
    def score_stars(self):
        avg = self.avg_score
        if avg is None:
            return ''
        full  = int(avg)
        half  = 1 if (avg - full) >= 0.5 else 0
        empty = 5 - full - half
        return '★' * full + ('½' if half else '') + '☆' * empty


class Drink(db.Model):
    __tablename__ = 'drinks'
    id            = db.Column(db.Integer, primary_key=True)
    dinner_id     = db.Column(db.Integer, db.ForeignKey('dinners.id'), nullable=False)
    type          = db.Column(db.String(20), default='vin')
    name          = db.Column(db.String(200))
    year          = db.Column(db.Integer, nullable=True)
    grape_type    = db.Column(db.String(100))
    tasting_notes = db.Column(db.Text)


class Score(db.Model):
    __tablename__ = 'scores'
    id        = db.Column(db.Integer, primary_key=True)
    dinner_id = db.Column(db.Integer, db.ForeignKey('dinners.id'), nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    score     = db.Column(db.Integer)
    comment   = db.Column(db.Text)
    __table_args__ = (db.UniqueConstraint('dinner_id', 'member_id',
                                          name='uq_score_dinner_member'),)


class Photo(db.Model):
    __tablename__ = 'photos'
    id          = db.Column(db.Integer, primary_key=True)
    dinner_id   = db.Column(db.Integer, db.ForeignKey('dinners.id'), nullable=False)
    filename    = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


class Quote(db.Model):
    __tablename__ = 'quotes'
    id        = db.Column(db.Integer, primary_key=True)
    dinner_id = db.Column(db.Integer, db.ForeignKey('dinners.id'), nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=True)
    text      = db.Column(db.Text, nullable=False)
    added_at  = db.Column(db.DateTime, default=datetime.utcnow)
    member    = db.relationship('Member')


class Recipe(db.Model):
    __tablename__ = 'recipes'
    id           = db.Column(db.Integer, primary_key=True)
    dinner_id    = db.Column(db.Integer, db.ForeignKey('dinners.id'), nullable=False)
    course       = db.Column(db.String(20))   # forrett | hoved | dessert
    title        = db.Column(db.String(200))
    ingredients  = db.Column(db.Text)          # én ingrediens per linje
    instructions = db.Column(db.Text)
    source_url   = db.Column(db.String(500))


class Award(db.Model):
    __tablename__ = 'awards'
    id          = db.Column(db.Integer, primary_key=True)
    year        = db.Column(db.Integer, nullable=False)
    category    = db.Column(db.String(100), nullable=False)
    winner      = db.Column(db.String(200))
    description = db.Column(db.Text)
    dinner_id   = db.Column(db.Integer, db.ForeignKey('dinners.id'), nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    dinner      = db.relationship('Dinner')


class HostDebt(db.Model):
    """Hvem skylder hvem middag — registrer bytte eller hopp"""
    __tablename__ = 'host_debts'
    id          = db.Column(db.Integer, primary_key=True)
    debtor_id   = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    creditor_id = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    note        = db.Column(db.String(300))
    settled     = db.Column(db.Boolean, default=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    debtor      = db.relationship('Member', foreign_keys=[debtor_id])
    creditor    = db.relationship('Member', foreign_keys=[creditor_id])
