package com.opsdesk.console

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val OpsBg = Color(0xFF0B0E14)
val OpsPanel = Color(0xFF131823)
val OpsPanel2 = Color(0xFF1A2130)
val OpsBorder = Color(0xFF242C3D)
val OpsText = Color(0xFFE6EDF7)
val OpsMuted = Color(0xFF8B98AD)
val OpsAccent = Color(0xFF4C8DFF)
val OpsGreen = Color(0xFF22C55E)
val OpsAmber = Color(0xFFF59E0B)
val OpsRed = Color(0xFFEF4444)

private val OpsColors = darkColorScheme(
    primary = OpsAccent,
    onPrimary = Color.White,
    secondary = OpsGreen,
    onSecondary = Color(0xFF05210F),
    tertiary = OpsAmber,
    background = OpsBg,
    onBackground = OpsText,
    surface = OpsPanel,
    onSurface = OpsText,
    surfaceVariant = OpsPanel2,
    onSurfaceVariant = OpsMuted,
    outline = OpsBorder,
    error = OpsRed,
    onError = Color.White
)

@Composable
fun OpsDeskTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = OpsColors, typography = Typography(), content = content)
}
