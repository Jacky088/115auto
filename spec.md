# 115 鑷姩杞瓨鍔╂墜 路 鍏ㄩ潰瀹¤鎶ュ憡

- 瀹¤鏃ユ湡锛?026-11-09
- 瀹¤鑼冨洿锛氬悗绔?FastAPI锛坅pi / core / worker / tg / pan115 / database / auth / config / main锛夈€侀儴缃诧紙Dockerfile / docker-compose / entrypoint / requirements锛夈€佸墠绔帴鍙ｅ绾︼紙router / api / 鍏抽敭瑙嗗浘锛夈€佹祴璇曞熀绾?- 瀹¤鏂瑰紡锛氶潤鎬佷唬鐮佸闃?+ 鏃㈡湁娴嬭瘯缁撴灉锛坄tests/test_worker_mock.py`锛?46 閫氳繃 / 2 澶辫触鐨勭绾垮缃戠敤渚嬶紝灞炲凡鐭ュ熀绾匡級
- 鏈疆缁撹锛?*鍙瀹¤锛屾湭淇敼浠讳綍浠ｇ爜**

## 涓€銆佹€讳綋缁撹

绯荤粺鏁翠綋鍙繍琛屻€佹ā鍧楄亴璐ｆ竻鏅帮紝鏃㈡湁瀹夊叏璁捐锛圥BKDF2 瀵嗙爜銆丣WT 杞崲銆佸嚟鎹枃浠?600銆佸浠芥帓闄ゅ瘑鐮佸搱甯屻€乄AL銆佽姹備綋涓婇檺锛夊凡鍏峰銆備富瑕侀闄╅泦涓湪**闃熷垪鐘舵€佹満绔炴€?*銆?*鐪嬮棬鐙楄鏉€闀夸换鍔?*銆?*浠ょ墝鍑虹幇鍦?URL**銆?*鐧诲綍闄愰€熷彲琚吉閫犲ご缁曡繃**銆?
| 绛夌骇 | 鏁伴噺 | 璇存槑 |
| --- | --- | --- |
| P0 | 0 | 鏃犻樆鏂€с€佸彲瀵艰嚧鏁版嵁涓嶅彲鎭㈠鐨勯棶棰?|
| P1 | 4 | 鍙鑷撮噸澶嶈浆瀛?棰濆害鍙岃銆佸嚟鎹硠闇层€侀鎺уけ鏁?|
| P2 | 9 | 鍙鐜版€с€佸畨鍏ㄥ姞鍥恒€佹€ц兘涓庢暟鎹竴鑷存€?|
| P3 | 7 | 鍙淮鎶ゆ€с€佸彲瑙傛祴鎬с€侀暱鏈熻啫鑳€ |

> 涓婁竴杞凡瀹屾垚鐨勮闃呰拷鍓т慨澶嶏紙鍒犻櫎鎸夐挳銆佸埆鍚嶃€佸璁㈤槄銆佺粨鏋勫寲闆嗘暟銆乀MDB 鍒嗙被鎺掑簭绛夛級涓嶅湪鏈缂洪櫡娓呭崟鍐呴噸澶嶈褰曘€?
## 浜屻€丳1 路 楂樹紭鍏堢骇

### P1-1 闃熷垪棰嗗彇涓庢垚鍔熷洖鍐欑己灏戠姸鎬佸畧鍗紙鍙噸澶嶈浆瀛?+ 棰濆害鍙岃锛?
- 鐜拌薄锛氶鍙栦换鍔℃椂鎵ц `UPDATE ... SET status='running' ... WHERE id=?`锛屾病鏈?`AND status='queued'`锛涙垚鍔熷洖鍐?`WHERE id=?`锛屼篃娌℃湁 `AND status='running'`銆?- 璇佹嵁锛歔queue_worker.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/queue_worker.py#L589-L596)銆乕queue_worker.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/queue_worker.py#L346-L349)
- 褰卞搷锛氬杩涚▼/澶氬壇鏈儴缃层€佹垨鐪嬮棬鐙楅噸鎺掑悗鍘熺嚎绋嬬◢鍚庤繑鍥烇紝閮戒細璁╁悓涓€浠诲姟琚袱涓墽琛屼綋鍚屾椂鎺ョ锛涙垚鍔熷洖鍐欎笉鐪嬪綋鍓嶇姸鎬侊紝浼氭妸宸查噸鎺掍换鍔″啀娆℃爣璁?`success` 骞朵簩娆?`quota_incr()`锛岄€犳垚**閲嶅杞瓨涓庨搴﹀鎵?*銆?- 淇寤鸿锛?  1. 棰嗗彇鏀逛负 `WHERE id=? AND status='queued'`锛宍changed==0` 鍗虫斁寮冿紱
  2. 鎴愬姛/澶辫触鍥炲啓缁熶竴鍔?`AND status='running'`锛宍changed==0` 瑙嗕负宸蹭綔搴燂紝涓嶅啓棰濆害锛?  3. `quota_incr()` 浠呭湪鎴愬姛鍥炲啓 `changed==1` 鏃舵墽琛岋紱
  4. 鍚屾椂娓呯悊 `next_retry_at`锛岄伩鍏嶅凡鎴愬姛浠诲姟琚噸璇曟壂鎻忔崱鍥炪€?- 楠屾敹锛氭ā鎷熶袱涓?worker 鍚屾椂棰嗗彇鍚屼竴 id锛屽彧鏈変竴涓兘杩涘叆 `running`锛涘欢杩熻繑鍥炵殑鏃х嚎绋嬫棤娉曟敼鍐欐渶缁堢姸鎬侊紱棰濆害璁℃暟涓庢垚鍔熻褰曚竴涓€瀵瑰簲銆?
### P1-2 鐪嬮棬鐙?30 鍒嗛挓闃堝€?+ 闀夸换鍔℃棤蹇冭烦锛堟甯稿ぇ浠诲姟琚閲嶆帓锛?
- 鐜拌薄锛氬仴搴锋鏌ユ瘡 2 鍒嗛挓鎶?`running_at < now-1800` 鐨勪换鍔＄洿鎺ラ噸鎺掞紱鑰?`_process_one` 鍦?`asyncio.to_thread` 涓繍琛岋紝鏃犳硶琚彇娑堬紝澶у垎浜紙鏁板崈鏂囦欢銆佸弽鏌?鎵惧洖/鏍￠獙锛夊緢瀹规槗瓒呰繃 30 鍒嗛挓銆?- 璇佹嵁锛歔jobs.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/jobs.py#L334-L341)銆乕queue_worker.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/queue_worker.py#L598-L612)
- 褰卞搷锛氫笌 P1-1 鑱斿姩浼氫骇鐢?*鍙屾墽琛屼綋銆侀噸澶嶆枃浠躲€佸弻棰濆害銆佸弻閫氱煡**锛涙敞閲婁腑鈥滀細琚悗缁畬鎴愪换鍔＄殑鐘舵€佸畧鍗嚜鐒朵綔搴熲€濅笌瀹炵幇涓嶇锛屽睘浜庤瀵兼€ф敞閲娿€?- 淇寤鸿锛氬湪杞瓨鍏抽敭闃舵锛堝揩鐓с€佹媺鍙栥€佽浆瀛樸€佹牎楠屻€佹暣鐞嗭級鍛ㄦ湡鎬у埛鏂?`running_at` 蹇冭烦锛涙妸闃堝€煎仛鎴愬彲閰嶇疆锛堥粯璁?30鈫?0 鍒嗛挓锛夛紱閲嶆帓鍓嶈褰?`watchdog_requeued_at`锛屾棫绾跨▼鍥炲啓琚畧鍗嫤涓嬨€?- 楠屾敹锛氭瀯閫犺€楁椂 >30 鍒嗛挓鐨?mock 浠诲姟锛岀‘璁や笉浼氳閲嶆帓锛涚湡姝ｅ崱姝荤殑浠诲姟浠嶈兘鍦ㄩ槇鍊煎悗鍥炴敹銆?
### P1-3 TMDB 娴锋姤鎺ュ彛鎶?JWT 鏀捐繘 URL 鏌ヨ鍙傛暟

- 鐜拌薄锛歚/api/tmdb/poster` 鍚屾椂鎺ュ彈 `Authorization` 鍜?`?token=`锛屽墠绔?`<img>` 浣跨敤 query 鏂瑰紡銆?- 璇佹嵁锛歔manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py#L834-L850)銆乕ws.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/ws.py)锛圵S 渚у埢鎰忕敤 Sec-WebSocket-Protocol 閬垮厤 URL 甯?token锛岃鏄庡洟闃熷凡鏈夋绾﹀畾锛?- 褰卞搷锛氶暱鏈?JWT 浼氳繘鍏?Nginx/FastAPI 璁块棶鏃ュ織銆佹祻瑙堝櫒鍘嗗彶銆丷eferer銆佷唬鐞嗙紦瀛橈紱涓€鏃︽棩蹇楄璇诲彇鍗冲彲鎺ョ鍚庡彴銆?- 淇寤鸿锛氱鍙?*鐙珛 scope銆?0 绉掓湁鏁堢殑涓€娆℃€ф捣鎶ョ鍚?*锛坄scope=poster&path=...`锛夛紝鎴栧湪鐧诲綍鏃朵笅鍙?HttpOnly + SameSite=Strict 鐨勪細璇?Cookie锛岀敱鍥剧墖璇锋眰鑷姩鎼哄甫锛涚Щ闄?query 涓殑涓?token锛涘搷搴斿姞 `Cache-Control: private, max-age=...`锛堝凡鏈夌鏈夌紦瀛橀€昏緫鍙鐢級銆?- 楠屾敹锛氬浘鐗囨甯稿姞杞斤紱璁块棶鏃ュ織涓嶅惈涓?token锛涗富 token 杩囨湡/瀵嗛挜杞崲鍚庢棫娴锋姤 URL 鑷姩澶辨晥銆?
### P1-4 鐧诲綍闄愰€熶俊浠诲鎴风鍙吉閫犵殑 X-Forwarded-For

- 鐜拌薄锛歚_client_ip()` 鏃犳潯浠跺彇 `X-Forwarded-For` 棣栨銆?- 璇佹嵁锛歔manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py#L17-L22)銆乕manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py#L41-L49)
- 褰卞搷锛氭敾鍑昏€呮瘡娆¤姹傛崲涓€涓吉閫?XFF 鍗冲彲**瀹屽叏缁曡繃鐧诲綍澶辫触闄愰€?*锛屼篃鍙薄鏌撴棩蹇楁潵婧愶紱鑻ュ墠缃唬鐞嗘湭瑕嗙洊璇ュご锛岄闄╃洿鎺ュ彲鍒╃敤銆?- 淇寤鸿锛氭柊澧炩€滃彲淇′唬鐞嗏€濋厤缃紙鐜鍙橀噺/璁剧疆椤癸級锛涗粎褰撶洿杩炴潵婧愬睘浜庡彲淇′唬鐞嗘椂鎵嶈В鏋?XFF锛屽苟鍙栨渶鍙充晶鍙俊娈碉紱鍚﹀垯涓€寰嬬敤 `request.client.host`銆倁vicorn 鍚姩鍙傛暟淇濇寔榛樿锛屼笉鍚敤 `--proxy-headers` 鐨勫鏉炬ā寮忋€?- 楠屾敹锛氫吉閫?XFF 杩炵画澶辫触 5 娆′粛瑙﹀彂 429锛涚湡瀹炲弽浠ｅ満鏅彲姝ｇ‘鍙栧埌瀹㈡埛绔?IP銆?
## 涓夈€丳2 路 涓紭鍏堢骇

### P2-1 渚濊禆鏈攣瀹氾紝閲嶅缓涓嶅彲澶嶇幇

- 璇佹嵁锛歔requirements.txt](file:///c:/Users/littl/Desktop/115autosave/backend/requirements.txt#L1-L10)
- 褰卞搷锛氬叏閮ㄤ负 `>=` 涓嬮檺锛宍p115client` 浠嶅 0.0.x銆乀elethon/FastAPI 璺ㄥぇ鐗堟湰鍗囩骇鍙兘鏀瑰彉琛屼负锛汥ocker 閲嶅缓缁撴灉涓庡凡楠岃瘉鐗堟湰涓嶄竴鑷达紝鏁呴殰闅惧洖婧€?- 寤鸿锛氱敓鎴?`requirements.lock`锛坄pip freeze` 鎴?`uv lock`锛夛紝闀滃儚瀹夎閿佸畾鐗堟湰锛汣I 瀹氭湡璺戝吋瀹规€у崌绾с€?
### P2-2 璇锋眰浣撲笂闄愪粎渚濊禆 Content-Length锛屽彲琚?chunked 缁曡繃

- 璇佹嵁锛歔main.py](file:///c:/Users/littl/Desktop/115autosave/backend/main.py#L86-L93)
- 褰卞搷锛氭湭璁よ瘉鐨?`/api/auth/login` 鍙敤鏃?`Content-Length` 鐨勫垎鍧楄姹傛寔缁彂閫侊紝FastAPI 浠嶄細鏁翠綋璇诲叆鍐呭瓨锛屽皬鍐呭瓨 NAS 鍙 OOM銆?- 寤鸿锛氬湪涓棿浠?ASGI 灞傛寜瀹為檯璇诲彇瀛楄妭璁℃暟鎴柇锛涙垨浣跨敤鍙嶅悜浠ｇ悊 `client_max_body_size`锛涙仮澶嶅浠界鐐瑰崟鐙檺娴佷笌澶у皬鏍￠獙銆?
### P2-3 淇敼瀵嗙爜鐨勯檺閫熸鏌ョ敤浜嗙┖ IP 妗讹紝瀹為檯涓嶇敓鏁?
- 璇佹嵁锛歔manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py#L66-L73)銆乕auth.py](file:///c:/Users/littl/Desktop/115autosave/backend/auth.py#L31)
- 褰卞搷锛氬凡鐧诲綍浼氳瘽鍙棤闄愭灏濊瘯鏃у瘑鐮侊紱`record_login_fail` 璁板湪鐪熷疄 IP 妗讹紝鑰屾鏌ヨ鐨勬槸 `""` 妗讹紝浜岃€呬笉鏄悓涓€涓鏁板櫒銆?- 寤鸿锛歚login_throttled(_client_ip(request))`锛屽苟鎶娾€滄棫瀵嗙爜閿欒鈥濊鍏ュ悓涓€ IP 妗躲€?
### P2-4 `/docs` 涓?`/openapi.json` 鏃犻壌鏉冨叕寮€

- 璇佹嵁锛歔main.py](file:///c:/Users/littl/Desktop/115autosave/backend/main.py#L81)
- 褰卞搷锛氬叕缃戞毚闇叉椂鎺ュ彛缁撴瀯銆佸弬鏁般€佸唴閮ㄩ敊璇ā鍨嬪叏閮ㄥ彲鏋氫妇锛堝惈瀵瑰 API 褰㈡€侊級锛屽鍔犳敾鍑婚潰銆?- 寤鸿锛歚FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`锛屾垨浠呯櫥褰曞悗鍙闂紙寮€鍙戠幆澧冨紑鍏筹級銆?
### P2-5 鍓嶇 JWT 瀛?localStorage锛屼笖鏈 CSP

- 璇佹嵁锛歔api.js](file:///c:/Users/littl/Desktop/115autosave/web/src/api.js#L7)銆乕Login.vue](file:///c:/Users/littl/Desktop/115autosave/web/src/views/Login.vue#L38)銆乕main.py](file:///c:/Users/littl/Desktop/115autosave/backend/main.py#L84-L99)锛堟槑纭笉鍔?CSP锛屽洜 Element Plus 鍐呰仈鏍峰紡锛?- 褰卞搷锛氫换浣?XSS锛堢涓夋柟鑴氭湰銆佸瘜鏂囨湰銆佷緷璧栨姇姣掞級鍙洿鎺ヨ鍙栭暱鏈?token锛涙棤 CSP 鏃剁己灏戞渶鍚庝竴閬撶紦瑙ｃ€?- 寤鸿锛氱煭鏈熷姞涓ユ牸 `script-src 'self'`銆乣object-src 'none'`銆乣base-uri 'self'`锛圗lement Plus 鐨勫唴鑱?*鏍峰紡**涓嶅彈 `style-src` 褰卞搷锛屽彲鐢?`style-src 'self' 'unsafe-inline'`锛夛紱涓湡鏀逛负 HttpOnly Cookie + CSRF Token銆?
### P2-6 Bot `/add` 閲嶅悕鍙鏌?polling 绫诲瀷

- 璇佹嵁锛歔bot_commands.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/bot_commands.py#L381-L385)
- 褰卞搷锛氬悓涓€棰戦亾鍙悓鏃跺瓨鍦?userbot 涓?polling 涓ゆ潯璁板綍锛屾秷鎭涓ゆ潯閾捐矾閲嶅澶勭悊锛屽彲鑳戒骇鐢熼噸澶嶄换鍔′笌閲嶅閫氱煡銆?- 寤鸿锛氶噸鍚嶆鏌ュ幓鎺?`type` 鏉′欢锛涘纭渶鍙岄摼璺紝鏄庣‘鍘婚噸绛栫暐锛坄messages` 涓婚敭宸叉寜棰戦亾+娑堟伅鍘婚噸锛屼絾涓ゆ潯璁板綍鐨勯閬撳悕瀛楁鍙兘涓嶅悓锛夈€?
### P2-7 澶辫触閫氱煡鍦ㄤ簨浠跺惊鐜唴鍚屾鎵ц

- 璇佹嵁锛歔queue_worker.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/queue_worker.py#L613-L623)
- 褰卞搷锛氭案涔呭け璐ュ垎鏀洿鎺ヨ皟鐢ㄥ悓姝?`jobs.notify_failure`锛? 涓笭閬?脳 瓒呮椂锛堟渶闀?15s/娓犻亾锛変細闃诲鏁翠釜闃熷垪寰幆锛屾湡闂撮噸璇曟壂鎻忋€佽皟搴︾獥鍙ｅ垽鏂叏閮ㄥ欢杩熴€?- 寤鸿锛氱粺涓€ `await asyncio.to_thread(jobs.notify_failure, ...)`锛涢€氱煡鍐呴儴鏀逛负骞跺彂鍙戦€佸苟璁炬€昏秴鏃躲€?
### P2-8 瀵瑰 API 鍏ラ槦缂哄皯骞跺彂鍐茬獊鍏滃簳

- 璇佹嵁锛歔ext.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/ext.py#L121-L132)銆乕pipeline.py](file:///c:/Users/littl/Desktop/115autosave/backend/core/pipeline.py#L144-L180)锛坧ipeline 鏈?`IntegrityError` 杞垽閲嶏紝ext 娌℃湁锛?- 褰卞搷锛氫袱涓苟鍙戣姹傚悓鏃堕€氳繃鈥滄煡閲嶁€濆悗鎻掑叆锛屼細瑙﹀彂閮ㄥ垎鍞竴绱㈠紩鍐茬獊锛岀洿鎺?500锛涜皟鐢ㄦ柟閲嶈瘯鍙堜細閫犳垚璇箟娣锋穯銆?- 寤鸿锛氭崟鑾?`sqlite3.IntegrityError` 骞舵寜 duplicate 杩斿洖锛涙彃鍏ュ悗鎸?`last_insert_rowid()` 鍙?id锛岃€屼笉鏄啀鏌?`status='queued'`锛堝綋鍓嶆煡閲嶅彲鑳藉彇鍒板埆浜虹殑浠诲姟锛夈€?
### P2-9 鍚屽悕鍒ら噸浣跨敤鍓嶇紑鍖归厤涓斿彧鍙栧墠 500 涓枃浠?
- 璇佹嵁锛歔queue_worker.py](file:///c:/Users/littl/Desktop/115autosave/backend/worker/queue_worker.py#L103-L118)
- 褰卞搷锛歚startswith(title)` 浼氭妸鈥滆繘鍑荤殑宸ㄤ汉 鏈€缁堝鈥濊鍒や负宸插瓨鍦ㄢ€滆繘鍑荤殑宸ㄤ汉鈥濓紱鐩綍瓒呰繃 500 涓枃浠舵椂婕忓垽瀵艰嚧閲嶅杞瓨銆?- 寤鸿锛氭瘮杈冨綊涓€鍖栧悗鐨勬枃浠跺悕锛堝幓鎵╁睍鍚嶃€佸幓瀛ｉ泦鏍囪锛夊仛绮剧‘鍖归厤锛涘垎椤垫媺鍏ㄩ噺鎴栬嚦灏戜笌 `share_snap` 涓婇檺涓€鑷淬€?
## 鍥涖€丳3 路 浣庝紭鍏堢骇

1. **鏃ュ織鎼滅储鏈浆涔?LIKE 閫氶厤绗?*锛歚message LIKE '%{search}%'`锛岀敤鎴疯緭鍏?`%` 浼氬叏琛ㄦ壂鎻忋€俒manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py#L1480-L1482)銆傚缓璁鐢?`_like_escape` + `ESCAPE '\'`銆?2. **瑙勫垯/璺敱淇濆瓨闈炰簨鍔?*锛氶€愭潯 `db.execute`锛屼腑閫斿紓甯镐細閮ㄥ垎鐢熸晥銆俒manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py#L892)銆傚缓璁寘 `db.transaction()`銆?3. **transfers 鏃犱繚鐣?褰掓。绛栫暐**锛氶噸澶嶉摼鎺ユ瘡娆″懡涓兘浼氬啓涓€鏉?`duplicate`锛岄暱鏈熻〃鑶ㄨ儉涓斾华琛ㄧ洏/鍒楄〃鍙樻參銆俒pipeline.py](file:///c:/Users/littl/Desktop/115autosave/backend/core/pipeline.py#L144-L180)銆傚缓璁寜鏈堝綊妗ｆ垨浠呬繚鐣欐瘡閾炬帴鏈€杩?N 鏉°€?4. **TMDB discover 鏃犲垪琛ㄧ紦瀛?*锛氭瘡娆″垎绫绘祻瑙堟渶澶?40 娆¤鎯呰姹傦紙浠呭淇℃伅鏈?24h 缂撳瓨锛夛紝寮辩綉/闄愭祦鏃堕灞忔參銆俒manage.py](file:///c:/Users/littl/Desktop/115autosave/backend/api/manage.py#L679-L831)銆傚缓璁鈥滃垎绫荤涓€椤碘€濆仛 5鈥?0 鍒嗛挓鐭紦瀛樸€?5. **娴嬭瘯渚濊禆澶栫綉**锛? 涓敤渚嬭闂?`t.me/s`锛岀绾垮繀澶辫触锛岄毦浠ヤ綔涓?CI 闃绘柇椤广€俒tests/test_worker_mock.py](file:///c:/Users/littl/Desktop/115autosave/tests/test_worker_mock.py)銆傚缓璁爣璁?`@pytest.mark.network` 鎴?mock銆?6. **榛樿瀵瑰鐩戝惉 0.0.0.0**锛歝ompose 榛樿鍏ㄩ儴缃戝崱锛屼笖鏃犺祫婧愰檺鍒?鏃ュ織杞浆銆俒docker-compose.yml](file:///c:/Users/littl/Desktop/115autosave/docker-compose.yml#L7-L11)銆傚缓璁粯璁?127.0.0.1锛屾枃妗ｅ己璋冨弽浠ｏ紝骞跺姞 `logging` 杞浆涓庡唴瀛樹笂闄愩€?7. **澶囦唤鎭㈠鐩存帴鎿嶄綔杩炴帴**锛歚restore_payload` 鍦ㄦ寔閿佹湡闂寸洿鎺ョ敤 `_conn`锛屾湭璧扮粺涓€灏佽锛涘紓甯告椂鍙兘鐣欎笅鍗婃仮澶嶇姸鎬併€俒database.py](file:///c:/Users/littl/Desktop/115autosave/backend/database.py#L266)銆傚缓璁暣浣撳寘浜嬪姟骞舵牎楠屽浠界増鏈?瀛楁鐧藉悕鍗曘€?
## 浜斻€佸凡纭鐨勮壇濂藉疄璺碉紙涓嶅垪涓虹己闄凤級

- 瀵嗙爜 PBKDF2 鏍￠獙銆佸け璐ラ檺閫熴€丣WT 瀵嗛挜杞崲銆佸垵濮嬮殢鏈哄瘑鐮佹枃浠舵敼瀵嗗嵆鍒犮€?- 鍚姩鏀剁揣 cookie / secret / session 鏂囦欢鏉冮檺涓?600銆?- 澶囦唤鎺掗櫎瀵嗙爜鍝堝笇涓庨€氱煡瀵嗛挜锛屾仮澶嶅悗寮哄埗 `running=False`銆?- SQLite WAL + 缁熶竴閿?+ 浜嬪姟灏佽锛涙棩蹇?7 澶┿€佹秷鎭?30 澶╂竻鐞嗐€?- 璇锋眰浣撲笂闄愪笌瀹夊叏鍝嶅簲澶达紙nosniff / DENY / Referrer-Policy锛夈€?- 鍓嶇 hash 璺敱锛岄潤鎬佹墭绠℃棤娣遍摼 404 椋庨櫓锛沇ebSocket token 璧板瓙鍗忚銆?
## 鍏€佸缓璁慨澶嶉『搴?
1. P1-1 + P1-2锛堝悓涓€鐘舵€佹満鏀归€狅紝蹇呴』涓€娆″畬鎴愬苟琛ユ祴璇曪級
2. P1-3 + P1-4 + P2-3锛堥壌鏉冧笌闄愰€燂級
3. P2-1 + P2-2锛堟瀯寤哄彲澶嶇幇涓?OOM 闃叉姢锛?4. P2-7 + P2-8 + P2-9锛堥槦鍒楃ǔ瀹氭€т笌鍑嗙‘鎬э級
5. P2-4/5/6 涓?P3 鎵归噺娓呯悊

## 涓冦€佺害鏉?
- 涓嶆敼鍙樼幇鏈夊姛鑳借涓轰笌鍓嶅悗绔帴鍙ｅ绾︼紱淇鍚庨渶閲嶆柊鏋勫缓鍓嶇骞堕噸鎵撳彂琛屽寘銆?- 鏁忔劅閰嶇疆缁х画浣跨敤瀵嗙爜妗嗭紱姣忔棩鎽樿鎸?Asia/Shanghai锛涜绀烘枃瀛楀叏灞€鍔犵矖銆?- 淇瀹屾垚闇€鍥炲綊 `tests/test_worker_mock.py`锛屽苟浠モ€滈櫎 2 涓凡鐭ュ缃戠敤渚嬪鍏ㄩ儴閫氳繃鈥濅负楠屾敹鍩虹嚎銆?
