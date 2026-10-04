"""Resource scope and the two human action boundaries; historical grants remain readable."""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

from . import journal, strategy, tasks

EFFECTS = frozenset({'read', 'contact', 'publish', 'edit'})
TARGETS = frozenset({'public_business', 'owned_account'})
SECRET_SURFACE = re.compile(
    r'password|mot de passe|passcode|\botp\b|\b2fa\b|captcha|secret|api.?key|'
    r'security|sécurité|securite', re.I)
IDENTITY_DELETION = re.compile(r'\b(?:delete|close|supprimer|fermer)\b.{0,20}\b(?:account|compte)\b', re.I)
SENSITIVE = re.compile(
    r'pay(?:ment|out)?\b|paiement|payer|rembours|refund|withdraw|retrait|transf|'
    r'bank|bancair|\biban\b|\bcvv\b|\bcvc\b|card number|numéro de carte|billing|checkout|'
    r'\bbuy\b|achet|\bsubscribe\b|paid subscription|abonn.*pay|\bsign\b.*contract|\bsigner\b.*contrat|'
    r'accept.*(?:paid|contract)|place.*order|\bowner(?:ship)?\b|propri[ée]t[ée]|propri[ée]taire|'
    r'(?:permanent|définitiv|definitiv).*(?:delet|supprim)|(?:delet|supprim).*(?:permanent|définitiv|definitiv)|'
    r'sign up|signup|\bregister\b|'
    r'cré(?:er|ation).*compte|cr(?:eer|eation).*compte|create.*account|nouveau compte|'
    r's[\x27\u2019 ]?inscrire|/registration\b|/inscription\b', re.I)


def _human(actor):
    import os
    if actor != 'human' or os.environ.get('OCTOPUS_WORKBENCH_READONLY') == '1':
        raise PermissionError('seul l’humain peut accorder ou modifier un mandat (hors consultation)')


def list_mandates(business, *, active=False):
    sql = 'SELECT * FROM operational_mandates WHERE business=?'
    if active:
        sql += " AND status='active'"
    rows = [dict(r) for r in journal.query(sql + ' ORDER BY id', (strategy._business(business),))]
    for r in rows:
        r['effects'] = json.loads(r['effects'])
        r['resource_keys'] = json.loads(r['resource_keys'])
    return rows


def grant(business, label, target, effects, *, actor, resource_keys=(), replaces=None):
    _human(actor)
    business = strategy._business(business)
    effects = sorted(set(effects))
    keys = sorted(set(resource_keys))
    if target not in TARGETS or not effects or set(effects) - EFFECTS:
        raise ValueError('cible ou effets du mandat invalides')
    if target == 'owned_account' and not keys:
        raise ValueError('sélectionnez des ressources ou * (comptes globaux)')
    with tasks._tx() as conn:
        if replaces is not None and not conn.execute('SELECT id FROM operational_mandates WHERE id=? AND business=?',
                                                      (int(replaces), business)).fetchone():
            raise ValueError('mandat à modifier introuvable pour cette activité')
        mid = int(conn.execute(
            'INSERT INTO operational_mandates (business,label,target,effects,resource_keys,granted_by,created_at) '
            'VALUES (?,?,?,?,?,?,?)',
            (business, strategy._text(label, 'label'), target, json.dumps(effects), json.dumps(keys), actor,
             time.time())).lastrowid)
        if replaces is not None:
            conn.execute("UPDATE operational_mandates SET status='revoked',revoked_at=? WHERE id=? AND business=?",
                         (time.time(), int(replaces), business))
            tasks._emit(conn, business, None, 'mandate.revoked', {'id': int(replaces), 'replacement': mid})
        tasks._emit(conn, business, None, 'mandate.granted', {'id': mid, 'target': target, 'effects': effects, 'resources': keys})
    return mid


def revoke(business, mandate_id, *, actor):
    _human(actor)
    with tasks._tx() as conn:
        changed = conn.execute("UPDATE operational_mandates SET status='revoked', revoked_at=? "
                               "WHERE id=? AND business=? AND status='active'",
                               (time.time(), int(mandate_id), business)).rowcount
        if changed:
            tasks._emit(conn, business, None, 'mandate.revoked', {'id': int(mandate_id)})


def covering(business, target, effect, resource_key=None):
    for m in list_mandates(business, active=True):
        if m['target'] == target and effect in m['effects']:
            if target == 'public_business' or resource_key in m['resource_keys'] or '*' in m['resource_keys']:
                return m
    return None


def account_authority(business, resource_key, effect):
    from . import resources
    resource = resources.get(resource_key)
    account = (resource or {}).get('web_account') or {}
    if not account or not account.get('enabled') or account.get('session_status') != 'connected':
        return None
    return covering(business, 'owned_account', effect, resource_key)


def qualify_public(channel_id, business, *, source_url, observed_text):
    """Called with actually acquired public text, never model-supplied classification.

    Matching observed provenance binds an endpoint within the human's resource scope.
    It cannot create a scope or verify the identity of a recipient.
    """
    if not covering(business, 'public_business', 'contact'):
        return False
    rows = journal.query('SELECT * FROM economic_channels WHERE id=? AND business=?', (channel_id, business))
    if not rows:
        return False
    ch = dict(rows[0])
    src, loc = urlsplit(source_url), urlsplit(ch['locator'] or '')
    if src.scheme != 'https' or src.username or src.password or not src.hostname:
        return False
    if ch['kind'] == 'email':
        email = str(ch['locator'] or '').removeprefix('mailto:').lower()
        local, sep, domain = email.partition('@')
        if not sep or domain != src.hostname.lower() or email not in observed_text.lower():
            return False
    elif ch['kind'] in ('website', 'browser_form'):
        if loc.scheme != 'https' or loc.netloc != src.netloc or loc.path != src.path:
            return False
    else:
        return False
    with tasks._tx() as conn:
        conn.execute('INSERT OR IGNORE INTO channel_authority '
                     '(channel_id,business,target,source_ref,qualified_at) VALUES (?,?,\'public_business\',?,?)',
                     (channel_id, business, source_url, time.time()))
        # Availability only. Explicit access is preserved, never broadened.
        conn.execute("UPDATE economic_channels SET status='active' WHERE id=? AND status='discovered'", (channel_id,))
        tasks._emit(conn, business, None, 'channel.qualified', {'channel_id': channel_id, 'source': source_url})
    return True


def bind_account(channel_id, business, resource_key):
    # Human-configured resource; the action and its mandate retain their activity.
    from . import resources
    r = resources.get(resource_key)
    a = (r or {}).get('web_account') or {}
    rows = journal.query('SELECT locator FROM economic_channels WHERE id=? AND business=?', (channel_id, business))
    if not a or not rows:
        raise PermissionError('compte ou canal inconnu')
    if urlsplit(rows[0]['locator']).hostname not in a['domains']:
        raise PermissionError('canal hors des domaines de la ressource')
    with tasks._tx() as conn:
        existing = conn.execute('SELECT target,resource_key FROM channel_authority WHERE channel_id=?', (channel_id,)).fetchone()
        if existing and (existing['target'] != 'owned_account' or existing['resource_key'] != resource_key):
            raise PermissionError('canal déjà lié à une autre ressource')
        conn.execute('INSERT OR IGNORE INTO channel_authority '
                     '(channel_id,business,target,resource_key,source_ref,qualified_at) '
                     "VALUES (?,?,'owned_account',?,?,?)", (channel_id, business, resource_key, r['locator'], time.time()))
        conn.execute("UPDATE economic_channels SET status='active' WHERE id=? AND status='discovered'", (channel_id,))


def authorize(channel, effect, *, description='', financial=False):
    if financial or SENSITIVE.search(description) or SECRET_SURFACE.search(description):
        return {'allowed': False, 'reason': 'Paiement, engagement financier ou création de compte : intervention humaine requise.'}
    rows = journal.query('SELECT * FROM channel_authority WHERE channel_id=? AND business=?',
                         (channel['id'], channel['business']))
    if not rows:
        return {'allowed': channel['status'] == 'active' and channel['access'] == 'act', 'mandate_id': None}
    binding = dict(rows[0])
    if binding['target'] == 'owned_account':
        mandate = account_authority(channel['business'], binding['resource_key'], effect)
    else:
        mandate = covering(channel['business'], 'public_business', effect)
    return {'allowed': channel['status'] == 'active' and mandate is not None,
            'mandate_id': mandate.get('id') if mandate else None, 'resource_key': binding.get('resource_key')}


def replace(business, mandate_id, label, target, effects, *, actor, resource_keys=()):
    return grant(business, label, target, effects, actor=actor, resource_keys=resource_keys, replaces=mandate_id)
