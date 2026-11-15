package io.github.jacky088.autosave115

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import java.net.URL

/**
 * WebView 壳：加载用户部署的面板地址，业务全部在服务端。
 *
 * 首次启动输入服务器地址（存本地），之后直接进入面板。
 * 登录态由 WebView Cookie 承载（与浏览器一致，登录一次持续有效）。
 * 错误页通过内部协议 autosave115://retry / autosave115://setup 与原生交互。
 */
class MainActivity : ComponentActivity() {

    private companion object {
        const val PREFS = "shell"
        const val KEY_URL = "server_url"
        const val KEY_LAST_INPUT = "last_input"
        const val DEEP_SETUP = "autosave115://setup"
        const val DEEP_RETRY = "autosave115://retry"
    }

    private var webView: WebView? = null
    private var serverUrl: String? = null
    private var filePathCallback: ValueCallback<Array<Uri>>? = null

    private val prefs by lazy { getSharedPreferences(PREFS, Context.MODE_PRIVATE) }

    private val fileChooser = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val uris = WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
        filePathCallback?.onReceiveValue(uris ?: arrayOf())
        filePathCallback = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val saved = prefs.getString(KEY_URL, null)
        if (saved.isNullOrEmpty()) showSetup() else loadPanel(saved)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                val wv = webView
                if (wv != null && wv.canGoBack()) wv.goBack() else finish()
            }
        })
    }

    override fun onPause() {
        CookieManager.getInstance().flush() // 会话 Cookie 落盘，重启不掉登录
        super.onPause()
    }

    // ---------- 首次启动：输入服务器地址 ----------

    private fun showSetup() {
        webView = null
        val density = resources.displayMetrics.density
        fun dp(v: Int) = (v * density).toInt()

        val title = TextView(this).apply {
            text = getString(R.string.app_name)
            textSize = 24f
            gravity = Gravity.CENTER
            setTextColor(Color.parseColor("#1e2a44"))
        }
        val subtitle = TextView(this).apply {
            text = "输入你部署的面板地址\n推荐 HTTPS，如 https://pan.example.com"
            textSize = 14f
            gravity = Gravity.CENTER
            setTextColor(Color.parseColor("#6b7a99"))
            setPadding(0, dp(8), 0, dp(20))
        }
        val input = EditText(this).apply {
            hint = "https://pan.example.com"
            inputType = InputType.TYPE_TEXT_VARIATION_URI
            setText(prefs.getString(KEY_LAST_INPUT, ""))
            setSingleLine()
        }
        val error = TextView(this).apply {
            setTextColor(Color.parseColor("#e05252"))
            textSize = 13f
            setPadding(0, dp(8), 0, 0)
            visibility = View.GONE
        }
        val note = TextView(this).apply {
            text = "地址即浏览器访问面板的网址；登录态将保存在本应用内"
            textSize = 13f
            gravity = Gravity.CENTER
            setTextColor(Color.parseColor("#6b7a99"))
            setPadding(0, dp(16), 0, 0)
        }
        val connect = Button(this).apply {
            text = "连 接"
            setBackgroundColor(Color.parseColor("#4f6bff"))
            setTextColor(Color.WHITE)
            setPadding(0, dp(12), 0, dp(12))
            setOnClickListener {
                val raw = input.text.toString().trim()
                val url = normalizeUrl(raw)
                if (url == null) {
                    error.text = "地址无效：请输入 http(s):// 开头的完整网址"
                    error.visibility = View.VISIBLE
                    return@setOnClickListener
                }
                error.visibility = View.GONE
                prefs.edit().putString(KEY_URL, url).putString(KEY_LAST_INPUT, raw).apply()
                loadPanel(url)
            }
        }

        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
        }
        box.addView(title)
        box.addView(subtitle)
        box.addView(input, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        box.addView(error)
        box.addView(connect, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(44)).apply {
            topMargin = dp(20)
        })
        box.addView(note, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(28), dp(24), dp(28), dp(24))
            setBackgroundColor(Color.WHITE)
        }
        root.addView(box)
        setContentView(root)
    }

    private fun normalizeUrl(raw: String): String? {
        var t = raw.trim()
        if (t.isEmpty()) return null
        if (!t.startsWith("http://") && !t.startsWith("https://")) t = "https://$t"
        return try {
            val u = URL(t)
            if (u.host.isNullOrBlank()) null else t.rstrip('/')
        } catch (e: Exception) {
            null
        }
    }

    private fun String.rstrip(c: Char): String = if (endsWith(c)) dropLast(1) else this

    // ---------- 面板加载 ----------

    @SuppressLint("SetJavaScriptEnabled")
    private fun loadPanel(url: String) {
        serverUrl = url
        val wv = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.cacheMode = WebSettings.LOAD_DEFAULT
            webViewClient = PanelClient()
            webChromeClient = PanelChrome()
            setDownloadListener { link, agent, disposition, mime, _ ->
                download(link, agent, disposition, mime)
            }
        }
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(wv, true)
        webView = wv
        setContentView(wv)
        wv.loadUrl(url)
    }

    private fun hostOf(url: String): String? =
        runCatching { URL(url).host.takeIf { it.isNotBlank() } }.getOrNull()

    private fun download(link: String, agent: String, disposition: String, mime: String) {
        try {
            val req = DownloadManager.Request(Uri.parse(link)).apply {
                addRequestHeader("User-Agent", agent)
                CookieManager.getInstance().getCookie(link)?.let { addRequestHeader("Cookie", it) }
                setMimeType(mime)
                setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                if (disposition.contains("attachment")) {
                    setTitle(URLUtilGuess(link))
                }
            }
            (getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(req)
            Toast.makeText(this, "已开始下载", Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            Toast.makeText(this, "下载失败: ${e.message ?: "未知错误"}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun URLUtilGuess(link: String): String =
        link.substringAfterLast('/').ifEmpty { "download" }

    // ---------- WebView 客户端 ----------

    private inner class PanelClient : WebViewClient() {
        override fun shouldOverrideUrlLoading(
            view: WebView,
            request: WebResourceRequest
        ): Boolean {
            val target = request.url
            // 内部协议：错误页交互
            when (target.scheme) {
                "autosave115" -> {
                    when (target.host) {
                        "retry" -> webView?.reload()
                        "setup" -> prefs.edit().remove(KEY_URL).apply().also { showSetup() }
                    }
                    return true
                }
                "http", "https" -> Unit
                else -> return openExternal(target) // t.me / mailto 等外部协议
            }
            // 站内地址留在壳内；站外（TMDB、t.me 等）交系统浏览器
            val inSite = hostOf(target.toString()) != null &&
                    hostOf(target.toString()) == hostOf(serverUrl ?: "")
            if (inSite) return false
            return openExternal(target)
        }

        override fun onReceivedError(
            view: WebView,
            request: WebResourceRequest,
            error: WebResourceError
        ) {
            if (!request.isForMainFrame) return
            showNetError(request.url.toString(), error.description?.toString() ?: "连接失败")
        }

        private fun openExternal(target: Uri): Boolean =
            try {
                startActivity(Intent(Intent.ACTION_VIEW, target))
                true
            } catch (e: Exception) {
                Toast.makeText(this@MainActivity, "无法打开链接", Toast.LENGTH_SHORT).show()
                true
            }
    }

    private fun showNetError(failedUrl: String, reason: String) {
        val esc = reason.replace("&", "&amp;").replace("<", "&lt;")
        val html = """
            <html><head><meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
              body { font-family: sans-serif; background:#f2f5fd; color:#1e2a44;
                     display:flex; flex-direction:column; justify-content:center;
                     align-items:center; height:92vh; margin:0; padding:0 24px; text-align:center; }
              h3 { margin:0 0 8px; }
              p { color:#6b7a99; font-size:13px; word-break:break-all; }
              a { display:inline-block; margin:10px 6px 0; padding:10px 22px; border-radius:10px;
                  text-decoration:none; font-size:14px; }
              .retry { background:#4f6bff; color:#fff; }
              .setup { background:#e8ecfd; color:#3d54e8; }
            </style></head><body>
            <h3>无法连接服务器</h3>
            <p>$esc</p>
            <p><a class="retry" href="$DEEP_RETRY">重新连接</a>
               <a class="setup" href="$DEEP_SETUP">更换地址</a></p>
            </body></html>
        """.trimIndent()
        webView?.loadDataWithBaseURL(null, html, "text/html", "utf-8", failedUrl)
    }

    // ---------- 文件选择 / 下载 ----------

    private inner class PanelChrome : WebChromeClient() {
        override fun onShowFileChooser(
            webView: WebView,
            callback: ValueCallback<Array<Uri>>,
            params: FileChooserParams
        ): Boolean {
            filePathCallback?.onReceiveValue(arrayOf())
            filePathCallback = callback
            val intent = params.createIntent().apply {
                addCategory(Intent.CATEGORY_OPENABLE)
            }
            try {
                fileChooser.launch(intent)
                return true
            } catch (e: Exception) {
                filePathCallback = null
                return false
            }
        }
    }

    // ---------- 生命周期 ----------

    override fun onDestroy() {
        webView?.apply {
            loadUrl("about:blank")
            (parent as? ViewGroup)?.removeAllViews()
            destroy()
        }
        webView = null
        super.onDestroy()
    }
}
