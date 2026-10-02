"""Human authority above concrete channels. No economic ranking and no implied rights.

Channel access=act remains an explicit legacy grant. Derived authority never writes act;
its live mandate, business, connected resource and concrete effect are checked each time.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

from . import journal, strategy, tasks

EFFECTS = frozenset({'read', 'contact', 'publish', 'edit'})
TARGETS = frozenset({'public_business', 'owned_account'})
# Security boundary, not a commercial/platform taxonomy. Fail closed for these effects.
SENSITIVE = re.compile(
    r'password|mot de passe|passcode|\botp\b|\b2fa\b|captcha|secret|api.?key|'
    r'pay(?:ment|out)?\b|paiement|payer|rembours|refund|withdraw|retrait|transf|'
    r'bank|bancair|\biban\b|\bcvv\b|\bcvc\b|card number|numéro de carte|billing|checkout|'
    r'buy\b|achet|advert|publicité|abonn|subscri|security|sécurité|securite|'
    r'delet|supprim|remove|contract|contrat|signature|sign up|signup|register|inscri|'
    r'accept.*(?:terms|conditions)|accepte.*conditions', re.I)
BUSINESS_SIGNALS = re.compile(r'\b(?:entreprise|société|societe|siret|siren|business|company|'
                              r'professionnel|professional|services|devis)\b', re.I)
CONTACT_SIGNALS = re.compile(r'contact|nous écrire|nous ecrire|get in touch', re.I)


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
    if target == 'public_business' and effects != ['contact']:
        raise ValueError('le périmètre public professionnel couvre uniquement le contact sans dépense')
    if target == 'owned_account' and not keys:
        raise ValueError('sélectionnez des ressources ou * (comptes explicitement ouverts à cette activité)')
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
    if (not account or not account.get('enabled') or account.get('session_status') != 'connected'
            or business not in account.get('businesses', [])):
        return None
    if effect != 'read' and account.get('ownership') not in ('operator', 'business'):
        return None
    return covering(business, 'owned_account', effect, resource_key)


def qualify_public(channel_id, business, *, source_url, observed_text):
    """Called with actually acquired public text, never model-supplied classification.

    A public page's labels qualify a contact endpoint within the human's broad mandate.
    They cannot create a mandate. This is deliberately conservative, not identity verification.
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
    if not BUSINESS_SIGNALS.search(observed_text) or not CONTACT_SIGNALS.search(observed_text):
        return False
    if ch['kind'] == 'email':
        email = str(ch['locator'] or '').removeprefix('mailto:').lower()
        local, sep, domain = email.partition('@')
        if (not sep or domain != src.hostname.lower() or local not in
                ('contact', 'info', 'hello', 'bonjour', 'commercial', 'sales', 'support')
                or email not in observed_text.lower()):
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
    # The resource is human-configured; the model cannot assign ownership or businesses.
    from . import resources
    r = resources.get(resource_key)
    a = (r or {}).get('web_account') or {}
    rows = journal.query('SELECT locator FROM economic_channels WHERE id=? AND business=?', (channel_id, business))
    if not a or business not in a.get('businesses', []) or not rows:
        raise PermissionError('ressource non ouverte à cette activité')
    if urlsplit(rows[0]['locator']).hostname not in a['domains']:
        raise PermissionError('canal hors des domaines de la ressource')
    with tasks._tx() as conn:
        conn.execute('INSERT OR IGNORE INTO channel_authority '
                     '(channel_id,business,target,resource_key,source_ref,qualified_at) '
                     "VALUES (?,?,'owned_account',?,?,?)", (channel_id, business, resource_key, r['locator'], time.time()))
        conn.execute("UPDATE economic_channels SET status='active' WHERE id=? AND status='discovered'", (channel_id,))


def authorize(channel, effect, *, description='', financial=False):
    if financial or SENSITIVE.search(description):
        return {'allowed': False, 'reason': 'action sensible réservée à une autorisation humaine distincte'}
    if channel['status'] == 'active' and channel['access'] == 'act':
        return {'allowed': True, 'mandate_id': None}
    rows = journal.query('SELECT * FROM channel_authority WHERE channel_id=? AND business=?',
                         (channel['id'], channel['business']))
    if not rows:
        return {'allowed': channel['status'] == 'active' and channel['access'] == 'act', 'mandate_id': None}
    binding = dict(rows[0])
    if binding['target'] == 'owned_account':
        mandate = account_authority(channel['business'], binding['resource_key'], effect)
    else:
        mandate = covering(channel['business'], 'public_business', effect) if effect == 'contact' else None
    return {'allowed': channel['status'] == 'active' and mandate is not None,
            'mandate_id': mandate['id'] if mandate else None, 'resource_key': binding.get('resource_key')}


def replace(business, mandate_id, label, target, effects, *, actor, resource_keys=()):
    return grant(business, label, target, effects, actor=actor, resource_keys=resource_keys, replaces=mandate_id)
