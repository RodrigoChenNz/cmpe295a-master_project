/* 11 Users and roles.
 *
 * The permission matrix is built from `PERMISSIONS` below, and every row of it
 * corresponds to an auth dependency that actually exists in the API — nothing
 * is drawn that the service does not enforce.
 *
 * **Rank rule**, mirrored from the API: you manage only accounts below your own
 * role and grant only roles below it. A super admin manages everyone. So a
 * manager runs staff and viewers, and the selects only offer what will work.
 *
 * **"Remove" is two different operations** and the screen says which it is
 * doing. Somebody who has signed in or decided anything is *deactivated*: their
 * sessions end at once, but their name stays on every price they approved,
 * because the audit log is append-only and that is the point of it. Only an
 * invitation nobody took up is actually deleted.
 */

import { useMemo, useState } from 'react';
import { api, ApiError } from '../lib/api';
import {
  canManageRole, initials, isManagerRole, plural, relative, ROLE_LABEL, ROLE_ORDER,
} from '../lib/format';
import { useResource } from '../lib/useStream';
import { usePageChrome, useShell, useStreamEvents } from '../components/AppShell';
import {
  Button, ConfirmModal, ErrorState, Modal, SkeletonRows,
} from '../components/ui';
import type { InviteIssued, Role, UserRow } from '../lib/types';
import s from './Users.module.css';

const ROLES: Role[] = ROLE_ORDER;

/** Each row is something the API genuinely enforces, named by the dependency
 *  that enforces it. If a permission is not in this list it is not checked. */
const PERMISSIONS: { label: string; allows: Role[]; enforcedBy: string }[] = [
  { label: 'View the console and the activity log', allows: ['superadmin', 'manager', 'staff', 'viewer'], enforcedBy: 'current_user' },
  { label: 'Edit products and shelf display names', allows: ['superadmin', 'manager', 'staff'], enforcedBy: 'require_editor' },
  { label: 'Pair and reassign shelf labels', allows: ['superadmin', 'manager', 'staff'], enforcedBy: 'require_editor' },
  { label: 'Add a position to the store layout', allows: ['superadmin', 'manager', 'staff'], enforcedBy: 'require_editor' },
  { label: 'Approve, override or reject a price', allows: ['superadmin', 'manager'], enforcedBy: 'require_manager' },
  { label: 'Set a product’s opening price', allows: ['superadmin', 'manager'], enforcedBy: 'require_manager' },
  { label: 'Edit pricing rules and guardrails', allows: ['superadmin', 'manager'], enforcedBy: 'require_manager' },
  { label: 'Edit the store layout and settings', allows: ['superadmin', 'manager'], enforcedBy: 'require_manager' },
  { label: 'Invite and manage staff and read-only users', allows: ['superadmin', 'manager'], enforcedBy: 'require_manager' },
  { label: 'Grant, remove or manage managers and super admins', allows: ['superadmin'], enforcedBy: 'rank rule' },
];

export function Users() {
  const { user, toast } = useShell();
  const isManager = isManagerRole(user.role);

  const res = useResource(() => api.users(), []);
  useStreamEvents(['users'], res.reload);

  const [busy, setBusy] = useState<string | null>(null);
  const [removing, setRemoving] = useState<UserRow | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [invite, setInvite] = useState({ username: '', email: '', full_name: '', role: 'viewer' });
  const [inviteLink, setInviteLink] = useState<{ username: string; url: string; expires_at: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  usePageChrome(
    {
      title: 'Users and roles',
      actions: isManager ? (
        <Button onClick={() => { setInviteOpen(true); setError(null); }}>Invite user</Button>
      ) : undefined,
    },
    [isManager],
  );

  const rows = res.data?.rows ?? [];
  const summary = useMemo(() => {
    const active = rows.filter((r) => r.is_active).length;
    return `${rows.length} ${plural(rows.length, 'person', 'people')} · ${active} active`;
  }, [rows]);

  // The directory is manager-only. A non-manager still gets the matrix, which
  // is the part of this screen that is about them.
  const forbidden = res.error !== null && /manager role/i.test(res.error);

  const setRole = async (row: UserRow, role: string) => {
    setBusy(row.username);
    try {
      await api.setUserRole(row.username, role);
      toast(`${row.full_name} is now ${ROLE_LABEL[role] ?? role}.`);
      res.reload();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not change the role.', 'bad');
      res.reload();
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!removing) return;
    const hard = canHardDelete(removing);
    setBusy(removing.username);
    setError(null);
    try {
      if (hard) {
        await api.deleteUser(removing.username);
        toast(`The invitation for ${removing.full_name} was withdrawn.`);
      } else {
        await api.setUserActive(removing.username, false);
        toast(`${removing.full_name} can no longer sign in. Their past decisions keep their name.`);
      }
      setRemoving(null);
      res.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove them.');
    } finally {
      setBusy(null);
    }
  };

  // Roles this user may hand out. A manager inviting a manager would only be
  // refused by the service, so it is not offered.
  const grantable = ROLES.filter((r) => canManageRole(user.role, r));

  const showLink = (issued: InviteIssued) =>
    setInviteLink({
      username: issued.username,
      url: `${window.location.origin}${issued.invite_path}`,
      expires_at: issued.expires_at,
    });

  const reissue = async (row: UserRow) => {
    setBusy(row.username);
    try {
      showLink(await api.reissueInvite(row.username));
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not create a new invite.', 'bad');
    } finally {
      setBusy(null);
    }
  };

  const sendInvite = async () => {
    setBusy('invite');
    setError(null);
    try {
      const result = await api.inviteUser(invite);
      showLink(result);
      setInviteOpen(false);
      setInvite({ username: '', email: '', full_name: '', role: 'viewer' });
      res.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not send the invitation.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={s.page}>
      {/* ── People ─────────────────────────────────────────────────────── */}
      {forbidden ? (
        <section className={`card ${s.forbidden}`}>
          <h2 className={s.cardTitle}>People with access</h2>
          <p className={s.forbiddenBody}>
            Only a store manager can see who has an account. You are signed in as{' '}
            {ROLE_LABEL[user.role]}. What each role can do is below.
          </p>
        </section>
      ) : res.error ? (
        <ErrorState
          title="Cannot load the people on this store"
          body={res.error}
          actions={<Button onClick={res.reload}>Try again</Button>}
        />
      ) : res.loading ? (
        <SkeletonRows rows={4} columns={5} />
      ) : (
        <section className={`card ${s.people}`}>
          <div className={s.peopleHead}>
            <div>
              <h2 className={s.cardTitle}>People with access</h2>
              <p className={s.cardSub}>{summary}</p>
            </div>
          </div>

          <div className={s.scroller}>
            <table className={s.table}>
              <thead>
                <tr>
                  <th className={`${s.th} ${s.thFirst}`}>Name</th>
                  <th className={s.th}>Email</th>
                  <th className={s.th}>Role</th>
                  <th className={s.th}>Status</th>
                  <th className={s.th}>Last active</th>
                  <th className={s.th}>Decisions</th>
                  <th className={`${s.th} ${s.thLast}`} />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const you = row.username === user.username;
                  // Whether this account is below you, which is what the
                  // service checks before every change to it.
                  const manageable = isManager && canManageRole(user.role, row.role);
                  return (
                    <tr key={row.username} data-inactive={!row.is_active || undefined}>
                      <td className={`${s.td} ${s.nameCell}`}>
                        <span className={s.nameInner}>
                          <span className={s.avatar}>{initials(row.full_name)}</span>
                          <span className={s.nameStack}>
                            <span className={s.name}>{row.full_name}</span>
                            {you && <span className={s.you}>You</span>}
                          </span>
                        </span>
                      </td>
                      <td className={`${s.td} ${s.email}`}>{row.email}</td>
                      <td className={s.td}>
                        <select
                          className={`field ${s.roleSelect}`}
                          value={row.role}
                          disabled={!manageable || you || busy === row.username}
                          aria-label={`Role for ${row.full_name}`}
                          onChange={(e) => setRole(row, e.target.value)}
                        >
                          {ROLES.map((r) => (
                            // Their current role always stays listed, so a
                            // read-only select still names it.
                            <option key={r} value={r} disabled={r !== row.role && !grantable.includes(r)}>
                              {ROLE_LABEL[r]}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className={s.td}>
                        <span className={s.pill} data-tone={statusTone(row)}>
                          {statusWord(row)}
                        </span>
                      </td>
                      <td className={`${s.td} ${s.when}`}>
                        {row.last_login_at ? relative(row.last_login_at) : 'Never'}
                      </td>
                      <td className={`${s.td} ${s.decisions}`}>{row.decisions}</td>
                      <td className={`${s.td} ${s.right}`}>
                        {manageable && !you && row.is_active && !row.can_sign_in && (
                          <button
                            type="button"
                            className={s.restoreBtn}
                            disabled={busy === row.username}
                            onClick={() => reissue(row)}
                          >
                            New invite link
                          </button>
                        )}
                        {manageable && !you && row.is_active && (
                          <button
                            type="button"
                            className={s.removeBtn}
                            onClick={() => { setRemoving(row); setError(null); }}
                          >
                            {canHardDelete(row) ? 'Withdraw invite' : 'Remove access'}
                          </button>
                        )}
                        {manageable && !you && !row.is_active && (
                          <button
                            type="button"
                            className={s.restoreBtn}
                            onClick={async () => {
                              setBusy(row.username);
                              try {
                                await api.setUserActive(row.username, true);
                                toast(`${row.full_name} can sign in again.`);
                                res.reload();
                              } finally {
                                setBusy(null);
                              }
                            }}
                          >
                            Restore
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── Permission matrix ──────────────────────────────────────────── */}
      <section className={`card ${s.matrix}`}>
        <div className={s.matrixHead}>
          <h2 className={s.cardTitle}>What each role can do</h2>
          <p className={s.cardSub}>
            Every row is checked by the service, not only hidden in the
            interface — a request that skips the console is refused the same way.
          </p>
        </div>

        <div className={s.scroller}>
          <table className={s.table}>
            <thead>
              <tr>
                <th className={`${s.th} ${s.thFirst}`}>Permission</th>
                {ROLES.map((r) => (
                  <th key={r} className={`${s.th} ${s.centre}`}>
                    {ROLE_LABEL[r]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {PERMISSIONS.map((perm) => (
                <tr key={perm.label}>
                  <td className={`${s.td} ${s.permCell}`}>
                    <span className={s.permInner}>
                      {perm.label}
                      <span className={s.enforcedBy} title="The dependency that enforces it">
                        {perm.enforcedBy}
                      </span>
                    </span>
                  </td>
                  {ROLES.map((r) => {
                    const yes = perm.allows.includes(r);
                    return (
                      <td key={r} className={`${s.td} ${s.centre}`}>
                        <span className={s.mark} data-yes={yes || undefined}>
                          {yes ? '✓' : '—'}
                        </span>
                        <span className="srOnly">{yes ? 'allowed' : 'not allowed'}</span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── Modals ─────────────────────────────────────────────────────── */}
      {removing && (
        <ConfirmModal
          title={canHardDelete(removing) ? `Withdraw ${removing.full_name}’s invitation?` : `Remove ${removing.full_name}’s access?`}
          confirmLabel={canHardDelete(removing) ? 'Withdraw invitation' : 'Remove access'}
          busy={busy === removing.username}
          error={error}
          onClose={() => { setRemoving(null); setError(null); }}
          onConfirm={remove}
          body={
            canHardDelete(removing) ? (
              <p>
                They never signed in and have decided nothing, so the account is
                deleted outright. You can invite them again at any time.
              </p>
            ) : (
              <>
                <p>
                  Their sessions end immediately and they cannot sign in again
                  until a manager restores them.
                </p>
                <p>
                  Their name stays on the{' '}
                  <strong>
                    {removing.decisions} {plural(removing.decisions, 'decision')}
                  </strong>{' '}
                  they made. The audit log is append-only — that is what makes it
                  worth having.
                </p>
              </>
            )
          }
        />
      )}

      {inviteOpen && (
        <Modal
          title="Invite someone"
          subtitle="They choose their own password; nobody else ever sets it."
          width={520}
          onClose={() => setInviteOpen(false)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setInviteOpen(false)}>Cancel</Button>
              <Button
                busy={busy === 'invite'}
                aria-disabled={!invite.username.trim() || !invite.email.trim() || !invite.full_name.trim() || undefined}
                onClick={sendInvite}
              >
                Create invitation
              </Button>
            </>
          }
        >
          <div className={s.inviteForm}>
            <Field label="Full name">
              <input
                data-autofocus
                className="field"
                value={invite.full_name}
                placeholder="Het Bhalala"
                onChange={(e) => setInvite((v) => ({ ...v, full_name: e.target.value }))}
              />
            </Field>
            <Field label="Email">
              <input
                className="field"
                type="email"
                value={invite.email}
                placeholder="het.bhalala@sjsu.edu"
                onChange={(e) => setInvite((v) => ({ ...v, email: e.target.value }))}
              />
            </Field>
            <Field label="Username" hint="Lower case, 2–32 characters.">
              <input
                className={`field ${s.monoInput}`}
                value={invite.username}
                placeholder="hbhalala"
                onChange={(e) =>
                  setInvite((v) => ({ ...v, username: e.target.value.toLowerCase().replace(/[^a-z0-9_.-]/g, '') }))
                }
              />
            </Field>
            <Field label="Role">
              <select
                className="field"
                value={invite.role}
                onChange={(e) => setInvite((v) => ({ ...v, role: e.target.value }))}
              >
                {grantable.map((r) => (
                  <option key={r} value={r}>
                    {ROLE_LABEL[r]}
                  </option>
                ))}
              </select>
            </Field>
            {error && <p className={s.formError} role="alert">{error}</p>}
          </div>
        </Modal>
      )}

      {inviteLink && (
        <Modal
          title="Pass this on"
          subtitle={`The invite link for ${inviteLink.username} is shown once.`}
          width={520}
          onClose={() => setInviteLink(null)}
          footer={
            <>
              <Button
                variant="secondary"
                onClick={() =>
                  navigator.clipboard
                    .writeText(inviteLink.url)
                    .then(() => toast('Invite link copied.'))
                    .catch(() => toast('Could not copy. Select the link instead.', 'bad'))
                }
              >
                Copy link
              </Button>
              <Button onClick={() => setInviteLink(null)}>Done</Button>
            </>
          }
        >
          <div className={s.tokenBody}>
            <code className={s.token}>{inviteLink.url}</code>
            <p>
              They open it, choose a password only they know, and are signed in.
              Only a hash of it is stored, so it cannot be shown again — if it is
              lost, send a new link from this table. It works once and expires{' '}
              {relative(inviteLink.expires_at)}.
            </p>
            <p className={s.tokenNote}>
              Until they do, their account shows as <strong>Invited</strong> and
              cannot sign in.
            </p>
          </div>
        </Modal>
      )}
    </div>
  );
}

/** A hard delete is only ever right for an invitation nobody took up. */
function canHardDelete(row: UserRow): boolean {
  return !row.can_sign_in && row.last_login_at === null && row.decisions === 0 && row.policy_rows === 0;
}

function statusWord(row: UserRow): string {
  if (!row.is_active) return 'Removed';
  if (row.can_sign_in) return 'Active';
  // No usable password, but they have signed in before — their credential was
  // reset rather than never set. "Invited" would be wrong, and the difference
  // matters: one needs a first invitation, the other needs a new one.
  return row.last_login_at ? 'No password' : 'Invited';
}

function statusTone(row: UserRow): string {
  if (!row.is_active) return 'off';
  return row.can_sign_in ? 'on' : 'wait';
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={s.field}>
      <label className={s.fieldLabel}>{label}</label>
      {children}
      {hint && <span className={s.fieldHint}>{hint}</span>}
    </div>
  );
}
