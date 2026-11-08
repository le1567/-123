package com.opsdesk.console

import android.content.Context

/**
 * 手机端本地配置。访问令牌只保存在应用私有目录（allowBackup=false），
 * 不写入日志、不上传；建议使用细粒度 PAT，仅勾选目标仓库的 Contents 读写权限。
 */
class Prefs(context: Context) {
    private val sp = context.applicationContext.getSharedPreferences("opsdesk_console", Context.MODE_PRIVATE)

    var repo: String
        get() = sp.getString("repo", "le1567/-123") ?: "le1567/-123"
        set(value) = sp.edit().putString("repo", value.trim().removePrefix("https://github.com/").trim('/')).apply()

    var branch: String
        get() = sp.getString("branch", "main") ?: "main"
        set(value) = sp.edit().putString("branch", value.trim().ifBlank { "main" }).apply()

    var token: String
        get() = sp.getString("token", "") ?: ""
        set(value) = sp.edit().putString("token", value.trim()).apply()

    fun isConfigured(): Boolean = repo.contains("/") && token.isNotBlank()

    fun save(repoValue: String, tokenValue: String, branchValue: String) {
        repo = repoValue
        token = tokenValue
        branch = branchValue
    }

    fun clearAll() {
        sp.edit().clear().apply()
    }
}
