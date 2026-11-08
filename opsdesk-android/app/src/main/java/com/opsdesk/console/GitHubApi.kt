package com.opsdesk.console

import android.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

class ApiException(val code: Int, override val message: String) : Exception(message)

data class RemoteFile(val sha: String, val bytes: ByteArray, val meta: JSONObject) {
    fun text(): String = bytes.toString(Charsets.UTF_8)
    fun json(): JSONObject = JSONObject(text())
}

/**
 * GitHub Contents API。手机端需要「目标仓库 Contents 读写」的细粒度 PAT。
 * 所有高危操作（指令/发布/吊销）在手机端用管理私钥签名，桌面端验签后才执行。
 */
class GitHubApi(
    private val repo: String,
    private val token: String,
    private val branch: String = "main",
    private val baseUrl: String = "https://api.github.com"
) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    private val jsonMedia = "application/json; charset=utf-8".toMediaType()

    private fun encodePath(path: String): String =
        path.trim('/').split('/').joinToString("/") {
            URLEncoder.encode(it, "UTF-8").replace("+", "%20")
        }

    private fun urlFor(path: String): String =
        "$baseUrl/repos/$repo/contents/${encodePath(path)}"

    private fun builder(url: String): Request.Builder =
        Request.Builder()
            .url(url)
            .header("Authorization", "Bearer $token")
            .header("Accept", "application/vnd.github+json")
            .header("X-GitHub-Api-Version", "2022-11-28")
            .header("User-Agent", "OpsDeskConsole/1.0")

    private fun failure(code: Int, body: String): ApiException {
        val message = try {
            JSONObject(body).optString("message", body)
        } catch (_: Exception) {
            body
        }
        val friendly = when (code) {
            401 -> "令牌无效或已过期，请重新生成 PAT"
            403 -> "权限不足或触发限流：$message"
            404 -> "仓库或文件不存在，请检查仓库名/分支/令牌权限"
            409 -> "写入冲突，请重试"
            422 -> "请求被 GitHub 拒绝：$message"
            else -> "GitHub 请求失败（$code）：$message"
        }
        return ApiException(code, friendly)
    }

    suspend fun getFile(path: String): RemoteFile? = withContext(Dispatchers.IO) {
        client.newCall(builder(urlFor(path)).get().build()).execute().use { response ->
            val body = response.body?.string() ?: ""
            if (response.code == 404) return@withContext null
            if (!response.isSuccessful) throw failure(response.code, body)
            val meta = JSONObject(body)
            val content = meta.optString("content", "")
            val bytes = if (meta.optString("encoding") == "base64" && content.isNotBlank()) {
                Base64.decode(content, Base64.DEFAULT)
            } else {
                val download = meta.optString("download_url", "")
                if (download.isNotEmpty()) downloadRaw(download) else ByteArray(0)
            }
            RemoteFile(meta.optString("sha", ""), bytes, meta)
        }
    }

    suspend fun getJson(path: String): JSONObject? = getFile(path)?.json()

    suspend fun getText(path: String): String? = getFile(path)?.text()

    private fun downloadRaw(url: String): ByteArray {
        val request = builder(url).header("Accept", "application/vnd.github.raw").get().build()
        client.newCall(request).execute().use { response ->
            val body = response.body?.bytes() ?: ByteArray(0)
            if (!response.isSuccessful) throw failure(response.code, body.toString(Charsets.UTF_8))
            return body
        }
    }

    suspend fun listDir(path: String): List<JSONObject> = withContext(Dispatchers.IO) {
        client.newCall(builder(urlFor(path)).get().build()).execute().use { response ->
            val body = response.body?.string() ?: "[]"
            if (response.code == 404) return@withContext emptyList()
            if (!response.isSuccessful) throw failure(response.code, body)
            val array = JSONArray(body)
            (0 until array.length()).mapNotNull { index -> array.optJSONObject(index) }
        }
    }

    suspend fun putFile(path: String, bytes: ByteArray, message: String, sha: String? = null): JSONObject =
        withContext(Dispatchers.IO) {
            val payload = JSONObject()
                .put("message", message)
                .put("branch", branch)
                .put("content", Base64.encodeToString(bytes, Base64.NO_WRAP))
            if (!sha.isNullOrBlank()) payload.put("sha", sha)
            val request = builder(urlFor(path))
                .put(payload.toString().toRequestBody(jsonMedia))
                .build()
            client.newCall(request).execute().use { response ->
                val body = response.body?.string() ?: "{}"
                if (!response.isSuccessful) throw failure(response.code, body)
                JSONObject(body)
            }
        }

    suspend fun putText(path: String, text: String, message: String, sha: String? = null): JSONObject =
        putFile(path, text.toByteArray(Charsets.UTF_8), message, sha)

    suspend fun putJson(path: String, doc: JSONObject, message: String, sha: String? = null): JSONObject =
        putText(path, doc.toString(2), message, sha)

    suspend fun deleteFile(path: String, message: String, sha: String) = withContext(Dispatchers.IO) {
        val payload = JSONObject().put("message", message).put("sha", sha).put("branch", branch)
        val request = builder(urlFor(path))
            .delete(payload.toString().toRequestBody(jsonMedia))
            .build()
        client.newCall(request).execute().use { response ->
            val body = response.body?.string() ?: ""
            if (!response.isSuccessful) throw failure(response.code, body)
        }
    }

    suspend fun testConnection(): String = withContext(Dispatchers.IO) {
        val request = builder("$baseUrl/repos/$repo").get().build()
        client.newCall(request).execute().use { response ->
            val body = response.body?.string() ?: "{}"
            if (!response.isSuccessful) throw failure(response.code, body)
            val info = JSONObject(body)
            "已连接 ${info.optString("full_name", repo)}（私有：${info.optBoolean("private", false)}）"
        }
    }
}
