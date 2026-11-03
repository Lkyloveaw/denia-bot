# Store / Service implementation contract

Task3 delegated store file only. Root implements service and plugin. All methods synchronous, network never in transaction. Constructor `Store(path=None)` defaults module `PATH=.../data/game_events.sqlite3`, initializes lazily on first method call; no side effects at import. All returned objects detached dicts. `ConflictError(ValueError)` for revision/generation conflicts. New target bot_id/id are positive decimal strings (max20 digits), kind group/private.

## Settings

`settings()->dict` defaults includes revision=0, enabled=True (global push), group_enabled=True, private_enabled=True, whitelist=[],blacklist=[]; poll_minutes=30; thresholds=[7,3,1]; notify_new=True; notify_on_subscribe=True; lookahead_days=45; max_new_per_cycle=8; remind_only_active=True; cover_mode='image'; include_description=False; use_forward=False; cover_max_total_mb=5; cover_max_width=720; source_sra/source_akedata/source_prts=True; include_gacha=True; urgent_enabled=False; urgent_trigger_days=1; urgent_repeat_minutes=120; urgent_daily_limit=3; quiet_start=23; quiet_end=8; aliases={}.

`save_settings(patch,revision)->dict` permits these keys only (ignore supplied revision only if extracted by caller); validate bools strictly, ints/ranges per spec, whitelist/blacklist list of up to500 matching `(group|private):[1-9][0-9]{0,19}`, de-dupe; aliases known game keys + nonempty label max40 chars. CAS transaction increments revision else ConflictError. No whole-config overwrite loss.

## Subscriptions

`subscriptions()->list[dict]`; `subscription(id)->dict|None`; `save_subscription(payload,revision=None)->dict`; `delete_subscription(id,revision)->bool`.

Returned subscription: `id` UUID, `revision`, `bot_id`,`kind`,`target_id`,`enabled` True, `games` list, `thresholds` None inherit/[]off, `include_keywords`/`exclude_keywords` lists, `cover_mode` None inherit/image/link/off; fields input only these plus id, reject unknown. New unique natural key bot_id/kind/target_id; if duplicate with no id use existing natural key with supplied revision required. New nonempty games required; updates games=[] are allowed (no sends). Cap200 sessions. Each newly added game creates UUID generation, removed game clears init/notification state; retained games preserve it. Any user-setting change during initialization resets initialization snapshot/progress for retained still-initializing games, not ready games. Sync revisions and generation validity on every write.

`game_state(sub_id,game_id)->dict|None` returns `generation,status` ('initializing'/'ready'), `initial` None or events list, `sent` uid list. `begin_initial(sub_id,game_id,generation,events)->bool` set snapshot only if stillNone/matchinggen. `initial_sent(sub_id,game_id,generation,uids)->bool` union sent progress. `finish_initial(sub_id,game_id,generation,baseline:dict[uid,state])->bool` write baseline and ready; each state `{new_done:bool,tier:int|None,end:float}`.

`states(sub_id,game_id,generation)->dict[uid,state]`; `record_plans(sub_id,game_id,generation,plans,at)->bool` plans from logic (`event,reasons,tier`), updates new_done OR new reason and min(previous,new tier), checks currentgen, merges only notification rows. No global snapshot save.

## Snapshots / logs

`snapshot(game_id)->dict|None`; `save_snapshot(snapshot)->None`; `snapshots()->list[dict]`. Snapshot matches SourceClient fields, JSON activities; no source fetching. Latest attempted error status may carry retained events.

`log_delivery(bot_id,kind,target_id,game_id,status,detail='',at=None)->None`; `deliveries(limit=30)->list[dict]` newest first; status success/failed/unknown/blocked. Error max300chars, no chat contents.

## Urgent

`urgent_targets()->list[dict]`; `urgent_target(bot_id,user_id)->dict|None`; `save_urgent(payload,revision=None)->dict`; `delete_urgent(bot_id,user_id,revision)->bool`.

Target fields bot_id,user_id,revision,enabled=True,games nonempty known game list,include_keywords/exclude_keywords,origin_session='' or group:N/private:N. No group union. Mutable same target CAS; states survive disable; delete clears. max200targets.

`urgent_states(bot_id,user_id)->dict[uid,state]` each event retained `{event:dict,acked:bool,last_sent:float}`. `observe_urgent(bot_id,user_id,events)->None` add missing states without overwriting ack. `ack_urgent(bot_id,user_id,scope,now)->list[str]` scope plain case-insensitive substring of name or uid; empty means all observed unexpired pending; unknown scope no effect.

`urgent_budget(bot_id,user_id,day)->dict` count (int),last_sent(float). `record_urgent_sent(bot_id,user_id,events,now,day)->None` increments target/day message count once and updates matching event last_sent WITHOUT overwriting ack concurrently set. Target budget global last_sent should be returned even if day just rolled (query max previousday) to enforce interval across midnight.

## Refresh jobs / runtime

`request_refresh(mode='data',games=None,bot_id='')->dict` validated mode data/check, known optional games; enforces at most1 pending/running job (existingjob returned) and 60sec cooldown after lastcreation (ConflictError); return id,status,mode,games,bot_id,created_at. `claim_job(now=None)->dict|None` atomic pending->running; running jobs older than600s eligible reclaim. `finish_job(id,result=None,error='')->None` status succeeded/failed with result/error.

`set_runtime(bots:list[str],at:float)->None`; `runtime()->dict` bots and at, initially empty. panel needs botids even before firstsubscription. `prune(now)->None` delete ended notify/urgent event states older30days and logs/jobs older30days; must not clear stillneeded init/current settings.

No methods send, no UI/framework dependencies. Tests with real tempSQLite, thread contention/CAS, generation changes, per-target daily budget and state ack race.

## Implementation amendments

- Retained still-initializing games receive a **new generation** when a changed user setting resets initialization; stale acknowledgements cannot finish the replacement snapshot. Ready games retain their generation.
- Existing subscription `bot_id/kind/target_id` are immutable. Moving a destination requires delete/create and a fresh baseline.
- `save_snapshot` preserves one detached `previous_snapshot` for diagnostics after a legal complete, nonstale empty snapshot; delivery still reads only current `activities`. No recursive history.
- `jobs(limit=20)->list[dict]` returns newest-first job status with decoded games/result; limit1–1000.
- Cover settings accept total1–5MiB and width64–720px. `log_delivery` permits empty game_id for aggregate urgent messages.
- `ack_urgent` returns matched UID strings (not display names).
