# 淇浠诲姟娓呭崟锛堟墽琛岀粨鏋滐級

瀵瑰簲瀹¤鎶ュ憡锛歔spec.md](file:///c:/Users/littl/Desktop/115autosave/spec.md)
鍘熷垯锛氫笉鏀瑰姛鑳借涔夈€佷笉鐮村潖鍓嶅悗绔绾︺€佷慨瀹岄噸鏂?`npm run build` 骞堕噸鎵撳彂琛屽寘銆?
## 绗竴鎵?路 P1

- [x] **T1 闃熷垪鐘舵€佹満鍔犲畧鍗?* 鈥?棰嗗彇/鎴愬姛/澶辫触/鍥為€€鍏ㄩ儴甯?`AND status=...`锛涗粎鎴愬姛瀹堝崼鍛戒腑鎵嶈棰濆害锛涙垚鍔熷悗娓?`next_retry_at`銆傛枃浠讹細[queue_worker.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/queue_worker.py)
- [x] **T2 闀夸换鍔″績璺充笌鐪嬮棬鐙楅槇鍊?* 鈥?鏂板姣?60 绉掑埛鏂?`running_at` 鐨勫績璺充换鍔★紱闃堝€兼敼涓?`running_timeout_min`锛堥粯璁?60锛岃寖鍥?15鈥?20锛夛紱淇璇鎬ф敞閲娿€傛枃浠讹細[queue_worker.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/queue_worker.py)銆乕jobs.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/jobs.py)
- [x] **T3 娴锋姤閴存潈鏀归€?* 鈥?鐧诲綍/鏀瑰瘑涓嬪彂 HttpOnly + SameSite=Strict銆佽矾寰勪粎闄?`/api/tmdb/poster` 鐨?Cookie锛涚Щ闄?URL 涓殑涓?token锛涙柊澧?`/api/auth/logout` 娓呯悊 Cookie銆傛枃浠讹細[manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py)銆乕auth.py](file:///c:/Users/littl/Desktop/115autosave/backend/auth.py)銆乕Subscriptions.vue](file:///c:/Users/littl/Desktop/115autosave/web/src/views/Subscriptions.vue)銆乕Layout.vue](file:///c:/Users/littl/Desktop/115autosave/web/src/components/Layout.vue)
- [x] **T4 鍙俊浠ｇ悊涓庣櫥褰曢檺閫?* 鈥?鏂板 `TRUSTED_PROXIES`锛涗粎鍙俊鐩磋繛鏉ユ簮瑙ｆ瀽 XFF 涓斿彇鏈€鍙充晶闈炲彲淇″湴鍧€锛涘惁鍒欑敤 socket 鍦板潃銆傛枃浠讹細[manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py)銆乕config.py](file:///c:/Users/littl/Desktop/115autosave/backend/config.py)銆乕docker-compose.yml](file:///c:/Users/littl/Desktop/115autosave/docker-compose.yml)

## 绗簩鎵?路 P2

- [x] **T5 渚濊禆閿佸畾** 鈥?鏂板 `backend/requirements.lock`锛堣繍琛屾椂渚濊禆绮剧‘鐗堟湰 + tzdata锛夛紝Docker 鏀逛负瀹夎閿佸畾鏂囦欢銆傛枃浠讹細[requirements.lock](file:///c:/Users/littl/Desktop/115autosave/backend/requirements.lock)銆乕Dockerfile](file:///c:/Users/littl/Desktop/115autosave/Dockerfile)
- [x] **T6 璇锋眰浣撶湡瀹炲瓧鑺傞檺鍒?* 鈥?鏂板 ASGI 涓棿浠舵寜瀹為檯璇诲彇瀛楄妭鎴柇锛屼笉渚濊禆 Content-Length锛涜秴闄愮ǔ瀹氳繑鍥?413銆傛枃浠讹細[main.py](file:///c:/Users/littl/Desktop/115autosave/backend/main.py)
- [x] **T7 淇敼瀵嗙爜闄愰€熺敓鏁?* 鈥?鏃у瘑鐮侀敊璇笌闄愰€熸鏌ョ粺涓€浣跨敤鐪熷疄鏉ユ簮 IP 妗躲€傛枃浠讹細[manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py)
- [x] **T8 鍏抽棴鍏紑 API 鏂囨。** 鈥?`docs_url/redoc_url/openapi_url` 鍏ㄩ儴缃┖銆傛枃浠讹細[main.py](file:///c:/Users/littl/Desktop/115autosave/backend/main.py)
- [x] **T9 CSP 涓?token 瀛樺偍鍔犲浐** 鈥?澧炲姞 `script-src 'self'`銆乣object-src 'none'`銆乣base-uri 'self'`銆乣frame-ancestors 'none'`锛涙牱寮忎繚鐣?unsafe-inline銆傛枃浠讹細[main.py](file:///c:/Users/littl/Desktop/115autosave/backend/main.py)
- [x] **T10 棰戦亾閲嶅悕妫€鏌?* 鈥?Bot `/add` 涓庣綉椤垫坊鍔犻兘鏀逛负鎸夐閬撳悕鍏ㄥ眬鍒ら噸锛岄伩鍏嶅悓涓€棰戦亾鍙岄摼璺噸澶嶅鐞嗐€傛枃浠讹細[bot_commands.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/bot_commands.py)銆乕manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py)
- [x] **T11 閫氱煡绉诲嚭浜嬩欢寰幆** 鈥?姘镐箙澶辫触閫氱煡鏀圭敤 `to_thread`锛沗notify_detail` 澶氭笭閬撳苟鍙?+ 20 绉掓€昏秴鏃躲€傛枃浠讹細[queue_worker.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/queue_worker.py)銆乕jobs.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/jobs.py)
- [x] **T12 瀵瑰 API 鍏ラ槦绔炴€佸厹搴?* 鈥?鏂板 `db.insert()` 杩斿洖鑷 id锛涙崟鑾?`IntegrityError` 鎸?duplicate 杩斿洖锛屼笉鍐?500銆傛枃浠讹細[ext.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/ext.py)銆乕database.py](file:///c:/Users/littl/Desktop/115autosave/backend/database.py)
- [x] **T13 鍚屽悕鍒ら噸绮剧‘鍖?* 鈥?褰掍竴鍖栧悗绮剧‘姣斿锛堝幓绌虹櫧/澶у皬鍐?鎵╁睍鍚嶏級锛屽彇娑堝墠缂€鍖归厤锛涚洰褰曡鍙栦笂闄愭彁鍗囧埌 5000銆傛枃浠讹細[queue_worker.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/queue_worker.py)

## 绗笁鎵?路 P3

- [x] **T14 鏃ュ織鎼滅储 LIKE 杞箟** 鈥?澶嶇敤 `_like_escape` + `ESCAPE '\'`锛岄伩鍏?`%` 瑙﹀彂鍏ㄨ〃鎵弿銆傛枃浠讹細[manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py)
- [x] **T15 瑙勫垯/璺敱淇濆瓨浜嬪姟鍖?* 鈥?璺敱鍏堝叏閲忔牎楠岋紝鍐嶅湪涓€涓簨鍔″唴 DELETE + executemany 鍐欏叆銆傛枃浠讹細[manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py)
- [x] **T16 transfers 淇濈暀绛栫暐** 鈥?姣忔棩娓呯悊淇濈暀 duplicate/skipped 7 澶╋紝success/failed 鍙拌处姘镐箙淇濈暀銆傛枃浠讹細[jobs.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/jobs.py)
- [x] **T17 TMDB 鍒嗙被棣栭〉鐭紦瀛?* 鈥?discover 缁撴灉 5 鍒嗛挓缂撳瓨锛堟渶澶?100 閿級锛岄檷浣?N+1 涓庨厤棰濇秷鑰椼€傛枃浠讹細[manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py)
- [x] **T18 澶栫綉鐢ㄤ緥绋冲畾璺宠繃** 鈥?t.me 杩斿洖涓嶈冻 15 鏉★紙閿欒椤碉級鍗虫寜缃戠粶涓嶅彲鐢ㄨ烦杩囷紝绂荤嚎鍙ǔ瀹氬叏缁裤€傛枃浠讹細[test_worker_mock.py](file:///c:/Users/littl/Desktop/115autosave/tests/test_worker_mock.py)
- [x] **T19 閮ㄧ讲榛樿缁戝畾涓庤祫婧愰檺鍒?* 鈥?鏂板鏃ュ織杞浆锛?0m脳3锛変笌鍙€?`mem_limit` 绀轰緥銆傗殸锔?鍕樿锛?026-11-13锛夛細銆岄粯璁や粎鐩戝惉 127.0.0.1銆嶄笌瀹為檯涓嶇鈥斺€攄ocker-compose 榛樿 `${PAN_BIND_IP:-0.0.0.0}`锛堝叏缃戝崱锛夛紝鍏綉閮ㄧ讲璇峰湪 `.env` 璁?`PAN_BIND_IP=127.0.0.1` 骞跺姞鍙嶅悜浠ｇ悊銆傛枃浠讹細[docker-compose.yml](file:///c:/Users/littl/Desktop/115autosave/docker-compose.yml)
- [x] **T20 澶囦唤鎭㈠鍔犲浐** 鈥?淇濇寔鏁翠綋浜嬪姟 + 鍒楃櫧鍚嶅崟锛涘浠戒腑鐨勫嚟鎹被閿竴寰嬭烦杩囷紝绂佹琚浠藉€艰鐩栥€傛枃浠讹細[database.py](file:///c:/Users/littl/Desktop/115autosave/backend/database.py)

## 楠岃瘉缁撴灉

- 鍚庣瀛楄妭缂栬瘧閫氳繃锛歚python -m compileall backend tests`
- 鍥炲綊娴嬭瘯锛歔test_worker_mock.py](file:///c:/Users/littl/Desktop/115autosave/tests/test_worker_mock.py) 鈥?**244 閫氳繃 / 0 澶辫触 / 4 璺宠繃锛堝缃戜笉鍙揪锛?*
- 涓撻」楠岃瘉锛歚/docs`銆乣/openapi.json` 杩斿洖 404锛涙棤 Cookie 娴锋姤 401銆佹湁 Cookie 姝ｅ父閴存潈锛沜hunked 2MB 璇锋眰杩斿洖 413锛涗吉閫?XFF 涓嶇敓鏁堛€佸彲淇′唬鐞嗚В鏋愭纭紱鍚屼竴浠诲姟浜屾棰嗗彇 `changed=0`锛涚浉浼肩墖鍚嶄笉璇垽銆佺簿纭悓鍚嶅懡涓?- 鍓嶇鏋勫缓锛歚npm run build` 鎴愬姛锛屼骇鐗╁凡鏇存柊鍒?`backend/static`
- 鍙戣鍖咃細宸查噸鏂版墦鍖?`115autosave.zip`

## 鍚庣画缁存姢

1. 鍗囩骇渚濊禆鍚庨噸鏂板湪骞插噣鐜 `pip freeze` 鏇存柊 `requirements.lock`锛屽苟璺戝洖褰掋€?2. 鍙嶅悜浠ｇ悊閮ㄧ讲鏃惰缃?`TRUSTED_PROXIES`锛堥€楀彿鍒嗛殧 IP/CIDR锛夛紝鍚﹀垯鐧诲綍闄愰€熸寜鐩磋繛 IP 璁＄畻銆?3. 榛樿浠呮湰鏈虹洃鍚細杩滅▼璁块棶璇风敤鍙嶅悜浠ｇ悊鎴?SSH 闅ч亾锛涘眬鍩熺綉鐩磋繛闇€鍦?`.env` 鍐?`PAN_BIND_IP=0.0.0.0`銆?
