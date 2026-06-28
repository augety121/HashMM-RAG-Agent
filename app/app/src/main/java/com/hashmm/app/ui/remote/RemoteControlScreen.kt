package com.hashmm.app.ui.remote

import android.annotation.SuppressLint
import android.app.Activity
import android.app.PictureInPictureParams
import android.content.Context
import android.content.ContextWrapper
import android.content.pm.ActivityInfo
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.Canvas
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.text.BasicTextField
import org.webrtc.SurfaceViewRenderer
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material.icons.automirrored.outlined.ExitToApp
import androidx.compose.material.icons.automirrored.outlined.VolumeUp
import androidx.compose.material.icons.outlined.ChevronRight
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Computer
import androidx.compose.material.icons.outlined.ContentPaste
import androidx.compose.material.icons.outlined.GridView
import androidx.compose.material.icons.outlined.Keyboard
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.KeyboardArrowUp
import androidx.compose.material.icons.outlined.Mouse
import androidx.compose.material.icons.outlined.AspectRatio
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.material.icons.outlined.NetworkCheck
import androidx.compose.material.icons.outlined.PictureInPictureAlt
import androidx.compose.material.icons.outlined.DesktopWindows
import androidx.compose.material.icons.outlined.GridView
import androidx.compose.material.icons.outlined.HelpOutline
import androidx.compose.material.icons.outlined.ScreenRotation
import androidx.compose.material.icons.outlined.SwapVert
import androidx.compose.material.icons.outlined.TouchApp
import androidx.compose.material.icons.outlined.PowerSettingsNew
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Send
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Security
import androidx.compose.material.icons.outlined.Apps
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.foundation.Image
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.data.remote.RemoteDevice
import com.hashmm.app.ui.components.HashMascot
import org.json.JSONObject
import android.graphics.Matrix
import org.webrtc.RendererCommon
import kotlin.math.absoluteValue

/**
 * 远程控制屏：以 viewer 身份投屏并控制桌面客户端。
 *  · PICK_DEVICE：列出同账号在线的被控端（host），点选连接
 *  · STREAMING：TextureView 渲染远端屏幕（支持缩放）；触摸映射为点击/拖拽/长按右键（坐标归一化 0..1000）
 *
 * 输入动作/坐标格式与 `desktop/remote-viewer.html` 一致：left_click / right_click / double_click /
 * left_click_drag，x/y/x2/y2 ∈ 0..1000，相对视频内容区（已扣除黑边）。
 */
@SuppressLint("ClickableViewAccessibility")
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RemoteControlScreen(
    onBack: () -> Unit,
    viewModel: RemoteControlViewModel = hiltViewModel()
) {
    val state by viewModel.ui.collectAsStateWithLifecycle()
    val track by viewModel.remoteTrack.collectAsStateWithLifecycle()
    val relayFrame by viewModel.relayFrame.collectAsStateWithLifecycle()
    val context = LocalContext.current

    LaunchedEffect(Unit) { viewModel.connect() }
    BackHandler { if (state.stage == RemoteStage.STREAMING) viewModel.backToDeviceList() else { viewModel.disconnect(); onBack() } }

    // 进入投屏：锁横屏 + 沉浸式全屏（隐藏状态栏/导航栏）；退出自动还原。
    val streaming = state.stage == RemoteStage.STREAMING
    if (streaming) {
        DisposableEffect(Unit) {
            val activity = context as? Activity
            val prev = activity?.requestedOrientation
            activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
            val controller = activity?.window?.let { WindowCompat.getInsetsController(it, it.decorView) }
            controller?.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            controller?.hide(WindowInsetsCompat.Type.systemBars())
            onDispose {
                activity?.requestedOrientation = prev ?: ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
                controller?.show(WindowInsetsCompat.Type.systemBars())
            }
        }
    }

    Scaffold(
        topBar = {
            if (!streaming) {
                TopAppBar(
                    title = { Text(if (state.stage == RemoteStage.PICK_DEVICE) "可控设备" else "远程控制", fontWeight = FontWeight.Bold) },
                    navigationIcon = {
                        IconButton(onClick = { viewModel.disconnect(); onBack() }) {
                            Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回")
                        }
                    },
                    actions = {
                        if (state.stage == RemoteStage.PICK_DEVICE) {
                            IconButton(onClick = { viewModel.refreshDevices() }) {
                                Icon(Icons.Outlined.Refresh, contentDescription = "刷新")
                            }
                        }
                    }
                )
            }
        }
    ) { padding ->
        Box(
            Modifier.fillMaxSize().then(if (streaming) Modifier else Modifier.padding(padding)),
        ) {
            when (state.stage) {
                RemoteStage.STREAMING -> {
                    var controlMode by remember { mutableStateOf("trackpad") }   // trackpad=虚拟鼠标(拖动移光标+箭头)；touch=触屏绝对点击
                    var fillMode by remember { mutableStateOf(false) }         // 默认「适应」=完整显示整个屏幕（不裁边）；可在设置切「铺满」
                    val cursor = remember { mutableStateOf(androidx.compose.ui.geometry.Offset(0.5f, 0.5f)) } // 归一化虚拟光标
                    RemoteVideo(viewModel = viewModel, track = track, relayFrame = relayFrame, state = state, controlMode = controlMode, cursor = cursor, fillMode = fillMode)
                    StreamingControls(
                        viewModel = viewModel,
                        state = state,
                        cursor = cursor,
                        controlMode = controlMode,
                        onModeChange = { controlMode = it },
                        fillMode = fillMode,
                        onFillModeChange = { fillMode = it },
                        onExit = { viewModel.backToDeviceList() },
                    )
                }
                RemoteStage.PICK_DEVICE -> DevicePicker(
                    devices = state.devices,
                    relayOn = state.relayOn,
                    onToggleRelay = { viewModel.setRemoteRelay(!state.relayOn) },
                    onPick = { viewModel.selectDevice(it.id) },
                    onRetry = { viewModel.refreshDevices() }
                )
                RemoteStage.ERROR -> CenterInfo(
                    title = "连接失败",
                    subtitle = state.message,
                    actionLabel = "重试",
                    onAction = { viewModel.connect() }
                )
                else -> CenterInfo(title = state.message.ifBlank { "正在连接…" }, loading = true)
            }
        }
    }
}

@Composable
private fun RemoteVideo(
    viewModel: RemoteControlViewModel,
    track: org.webrtc.VideoTrack?,
    relayFrame: ImageBitmap?,
    state: RemoteUiState,
    controlMode: String,
    cursor: androidx.compose.runtime.MutableState<androidx.compose.ui.geometry.Offset>,
    fillMode: Boolean,
) {
    // 视频原生分辨率（由 RendererEvents 回填）用于触摸归一化
    var frameW by remember { mutableStateOf(state.remoteW) }
    var frameH by remember { mutableStateOf(state.remoteH) }
    var boxW by remember { mutableStateOf(0) }
    var boxH by remember { mutableStateOf(0) }
    var svr by remember { mutableStateOf<SurfaceViewRenderer?>(null) }

    // 切换/清理视频轨的 sink
    DisposableEffect(track, svr) {
        val r = svr
        if (r != null && track != null) track.addSink(r)
        onDispose { if (r != null && track != null) try { track.removeSink(r) } catch (_: Exception) {} }
    }
    // 离开投屏时释放渲染器的 EGL 资源
    DisposableEffect(Unit) { onDispose { svr?.release() } }

    // 缩放状态先声明（下面的 LaunchedEffect 复位会用到，Kotlin 局部变量必须先声明后用）
    var zoomScale by remember { mutableFloatStateOf(1f) }
    var zoomPan by remember { mutableStateOf(androidx.compose.ui.geometry.Offset.Zero) }

    // 中继模式下用中继帧的宽高做触摸归一化（帧保留远端屏幕宽高比）
    LaunchedEffect(relayFrame) {
        val rf = relayFrame
        if (rf != null) { frameW = rf.width; frameH = rf.height }
        else { zoomScale = 1f; zoomPan = androidx.compose.ui.geometry.Offset.Zero }
    }

    // 切「适应/铺满」时复位缩放与平移：保证一键「适应」=完整看到整个桌面（之前放大后看着像"只有部分"）。
    LaunchedEffect(fillMode) { zoomScale = 1f; zoomPan = androidx.compose.ui.geometry.Offset.Zero }

    // 缩放/平移下，把屏幕坐标反算回未变换的画面坐标，保证点击落点正确；scale==1 时原样返回（不缩放=行为完全不变）
    fun mapX(sx: Float): Float = if (zoomScale == 1f) sx else (boxW / 2f + (sx - boxW / 2f - zoomPan.x) / zoomScale)
    fun mapY(sy: Float): Float = if (zoomScale == 1f) sy else (boxH / 2f + (sy - boxH / 2f - zoomPan.y) / zoomScale)

    fun curXY(): Pair<Int, Int> = Pair((cursor.value.x * 1000).toInt().coerceIn(0, 1000), (cursor.value.y * 1000).toInt().coerceIn(0, 1000))

    Box(
        Modifier
            .fillMaxSize()
            // 不要在这里铺不透明底色：SurfaceViewRenderer 的画面在窗口"下层"，
            // 盖一层不透明背景会把它整个挡住 → 黑屏。保持透明，让视频透出来；光标/中继叠在上层照常显示。
            .onSizeChanged { boxW = it.width; boxH = it.height }
            .pointerInput(controlMode, frameW, frameH, boxW, boxH) {
                detectTapGestures(
                    onTap = { o ->
                        if (controlMode == "trackpad") { val (x, y) = curXY(); viewModel.sendClickAt("left_click", x, y) }
                        else send(viewModel, "left_click", mapX(o.x), mapY(o.y), boxW, boxH, frameW, frameH, fillMode)
                    },
                    onDoubleTap = { o ->
                        if (controlMode == "trackpad") { val (x, y) = curXY(); viewModel.sendClickAt("double_click", x, y) }
                        else send(viewModel, "double_click", mapX(o.x), mapY(o.y), boxW, boxH, frameW, frameH, fillMode)
                    },
                    onLongPress = { o ->
                        if (controlMode == "trackpad") { val (x, y) = curXY(); viewModel.sendClickAt("right_click", x, y) }
                        else send(viewModel, "right_click", mapX(o.x), mapY(o.y), boxW, boxH, frameW, frameH, fillMode)
                    },
                )
            }
            .pointerInput(controlMode, frameW, frameH, boxW, boxH) {
                if (controlMode == "trackpad") {
                    // 虚拟鼠标：未放大时单指拖=相对移光标；放大后单指拖=平移画面（滑动看全屏不同部位）
                    detectDragGestures(
                        onDrag = { change, drag ->
                            change.consume()
                            if (zoomScale > 1.01f) {
                                zoomPan = zoomPan + drag
                            } else {
                                val w = if (boxW > 0) boxW else 1
                                val h = if (boxH > 0) boxH else 1
                                val nx = (cursor.value.x + drag.x / w * 1.8f).coerceIn(0f, 1f)
                                val ny = (cursor.value.y + drag.y / h * 1.8f).coerceIn(0f, 1f)
                                cursor.value = androidx.compose.ui.geometry.Offset(nx, ny)
                                viewModel.sendMouseMove((nx * 1000).toInt(), (ny * 1000).toInt())
                            }
                        },
                    )
                } else {
                    // 触屏：拖拽 = 绝对位置框选/拖动
                    var startX = 0f; var startY = 0f; var curX = 0f; var curY = 0f
                    detectDragGestures(
                        onDragStart = { o -> startX = o.x; startY = o.y; curX = o.x; curY = o.y },
                        onDrag = { change, _ -> curX = change.position.x; curY = change.position.y },
                        onDragEnd = {
                            val a = norm(mapX(startX), mapY(startY), boxW, boxH, frameW, frameH, fillMode)
                            val b = norm(mapX(curX), mapY(curY), boxW, boxH, frameW, frameH, fillMode)
                            viewModel.sendInput(
                                JSONObject().put("action", "left_click_drag")
                                    .put("x", a.first).put("y", a.second)
                                    .put("x2", b.first).put("y2", b.second)
                            )
                        },
                    )
                }
            }
            .pointerInput(Unit) {
                // 双指缩放/平移：仅在 ≥2 指时消费事件，单指照常做点击/拖拽输入，互不打架
                awaitEachGesture {
                    awaitFirstDown(requireUnconsumed = false)
                    do {
                        val event = awaitPointerEvent()
                        if (event.changes.count { it.pressed } >= 2) {
                            zoomScale = (zoomScale * event.calculateZoom()).coerceIn(1f, 4f)
                            zoomPan = if (zoomScale <= 1.01f) androidx.compose.ui.geometry.Offset.Zero
                            else zoomPan + event.calculatePan()
                            event.changes.forEach { it.consume() }
                        }
                    } while (event.changes.any { it.pressed })
                }
            },
        contentAlignment = Alignment.Center
    ) {
        val rf = relayFrame
        // WebRTC 用 WebRTC 官方的 SurfaceViewRenderer 渲染（稳定显示、不黑屏）——这是 P2P 之前能出画面的渲染器。
        // 缩放/平移改用 View 级 scale/translation（SurfaceView 在 API24+ 会跟随 View 变换），不再用自定义 TextureView。
        // 关键修复（看全整个桌面）：不再依赖渲染器内部 SCALE_ASPECT_FIT 做留黑（对你的机器没生效），
        // 而是把渲染器 View 直接按"画面宽高比"摆放（aspectRatio）→ 整屏内居中、必定显示完整画面（含任务栏）。
        // 渲染器内部统一用 FILL（此时 View 已经是画面比例，FILL=刚好铺满、不裁切）。
        val ar = if (frameW > 0 && frameH > 0) frameW.toFloat() / frameH else 16f / 9f
        val screenWider = if (boxW > 0 && boxH > 0) boxW.toFloat() / boxH > ar else true
        AndroidView(
            factory = { ctx ->
                SurfaceViewRenderer(ctx).apply {
                    // 关键修复：SurfaceView 默认在窗口"下层"，被上层任何不透明背景/主题挡住就黑屏。
                    // setZOrderMediaOverlay(true) 把视频面提到窗口背景之上、但仍在 Compose 叠层(光标/控件)之下，
                    // 既能出画面又不挡叠层。必须在 init 之前调用。
                    setZOrderMediaOverlay(true)
                    init(viewModel.eglBase.eglBaseContext, object : RendererCommon.RendererEvents {
                        override fun onFirstFrameRendered() {}
                        override fun onFrameResolutionChanged(w: Int, h: Int, rotation: Int) {
                            val rotated = rotation == 90 || rotation == 270
                            frameW = if (rotated) h else w
                            frameH = if (rotated) w else h
                        }
                    })
                    setEnableHardwareScaler(true)
                    setScalingType(RendererCommon.ScalingType.SCALE_ASPECT_FILL)
                    svr = this
                }
            },
            update = { v ->
                v.setScalingType(RendererCommon.ScalingType.SCALE_ASPECT_FILL)
                v.scaleX = zoomScale; v.scaleY = zoomScale
                v.translationX = zoomPan.x; v.translationY = zoomPan.y
            },
            // 「适应」：View 取画面宽高比，整屏内居中→看到完整桌面；「铺满」：填满屏幕（可能裁边）。
            modifier = if (fillMode) Modifier.fillMaxSize()
                       else Modifier.aspectRatio(ar, matchHeightConstraintsFirst = screenWider)
        )
        if (track == null && rf != null) {
            // 中继画面是普通 Image，盖在上层，可安全套 graphicsLayer 做缩放/平移
            Image(
                bitmap = rf,
                contentDescription = "远程画面（中继）",
                contentScale = if (fillMode) ContentScale.Crop else ContentScale.Fit,
                modifier = Modifier.fillMaxSize().graphicsLayer {
                    scaleX = zoomScale; scaleY = zoomScale
                    translationX = zoomPan.x; translationY = zoomPan.y
                },
            )
        }
        // 远程光标箭头：始终画在 cursor 处（随拖动移动）。帧分辨率未知时退化为按整框计算，保证箭头一定可见。
        if (boxW > 0 && boxH > 0) {
            val hasFrame = frameW > 0 && frameH > 0
            val fa = if (hasFrame) frameW.toFloat() / frameH else boxW.toFloat() / boxH
            val ba = boxW.toFloat() / boxH
            val fitW = if (fillMode || !hasFrame) boxW.toFloat() else (if (fa > ba) boxW.toFloat() else boxH * fa)
            val fitH = if (fillMode || !hasFrame) boxH.toFloat() else (if (fa > ba) boxW / fa else boxH.toFloat())
            val fitLeft = (boxW - fitW) / 2f
            val fitTop = (boxH - fitH) / 2f
            val ax = fitLeft + cursor.value.x * fitW
            val ay = fitTop + cursor.value.y * fitH
            val axT = boxW / 2f + (ax - boxW / 2f) * zoomScale + zoomPan.x
            val ayT = boxH / 2f + (ay - boxH / 2f) * zoomScale + zoomPan.y
            Canvas(Modifier.offset { IntOffset(axT.toInt(), ayT.toInt()) }.size(26.dp)) {
                val w = size.width; val h = size.height
                val p = Path().apply {
                    moveTo(w * 0.04f, h * 0.02f); lineTo(w * 0.04f, h * 0.80f)
                    lineTo(w * 0.26f, h * 0.60f); lineTo(w * 0.40f, h * 0.95f)
                    lineTo(w * 0.54f, h * 0.89f); lineTo(w * 0.39f, h * 0.55f)
                    lineTo(w * 0.66f, h * 0.55f); close()
                }
                drawPath(p, Color(0xF2000000), style = Stroke(width = 6f))   // 深色描边（任何底色上都看得见）
                drawPath(p, Color.White)                                      // 白色填充
            }
        }
    }
}

/** TextureView 画面变换：先把被拉伸的帧缩回真实宽高比（适应/铺满），再围绕中心叠加缩放与平移。 */
private fun buildVideoMatrix(viewW: Int, viewH: Int, frameW: Int, frameH: Int, fill: Boolean, zoom: Float, panX: Float, panY: Float): Matrix {
    val m = Matrix()
    if (viewW <= 0 || viewH <= 0 || frameW <= 0 || frameH <= 0) return m
    val viewAspect = viewW.toFloat() / viewH
    val frameAspect = frameW.toFloat() / frameH
    var sx = 1f; var sy = 1f
    if (!fill) {
        if (frameAspect > viewAspect) sy = viewAspect / frameAspect
        else sx = frameAspect / viewAspect
    } else {
        if (frameAspect > viewAspect) sx = frameAspect / viewAspect
        else sy = viewAspect / frameAspect
    }
    val cx = viewW / 2f; val cy = viewH / 2f
    m.setScale(sx, sy, cx, cy)
    if (zoom != 1f) m.postScale(zoom, zoom, cx, cy)
    if (panX != 0f || panY != 0f) m.postTranslate(panX, panY)
    return m
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BoxScope.StreamingControls(
    viewModel: RemoteControlViewModel,
    state: RemoteUiState,
    cursor: androidx.compose.runtime.MutableState<androidx.compose.ui.geometry.Offset>,
    controlMode: String,
    onModeChange: (String) -> Unit,
    fillMode: Boolean,
    onFillModeChange: (Boolean) -> Unit,
    onExit: () -> Unit,
) {
    val accent = Color(0xFFEF3E36)
    val panelBg = Color(0xE6121A2A)
    val context = LocalContext.current
    var panel by remember { mutableStateOf("") } // "", keyboard, keys, system, scroll
    var toast by remember { mutableStateOf<String?>(null) }
    var showSettings by remember { mutableStateOf(false) }
    var showGuide by remember { mutableStateOf(false) }
    var autoHide by remember { mutableStateOf(false) }  // 默认关：工具栏常驻（用户反馈"操作按钮没了"）；想看全屏可在设置里开自动隐藏
    var barsVisible by remember { mutableStateOf(true) }
    var rotated by remember { mutableStateOf(false) }
    var keyMapping by remember { mutableStateOf(false) }   // UU：启用按键映射（游戏用，UI 开关）
    var tabletTouch by remember { mutableStateOf(false) }  // UU：平板触控模式（多点触控，UI 开关）
    var settingsPage by remember { mutableStateOf("main") }   // main / security / winops
    var secFingerprint by remember { mutableStateOf(false) }
    var secAutoUnlock by remember { mutableStateOf(false) }
    var secLockOnEnd by remember { mutableStateOf(false) }
    var secMute by remember { mutableStateOf(false) }
    var secAntiPeek by remember { mutableStateOf(false) }
    LaunchedEffect(toast) { if (toast != null) { kotlinx.coroutines.delay(1400); toast = null } }
    // 自动隐藏工具栏：开启后、面板收起且工具栏可见时，无操作 3.5s 自动收起。
    LaunchedEffect(autoHide, barsVisible, panel) {
        if (autoHide && barsVisible && panel.isEmpty()) { kotlinx.coroutines.delay(3500); barsVisible = false }
    }

    fun latencyColor(ms: Int) = when { ms < 0 -> Color.White; ms <= 80 -> Color(0xFF8FE3A0); ms <= 200 -> Color(0xFFFFD479); else -> Color(0xFFFF9F45) }

    // 收起态：左上角小手柄，点开恢复工具栏（同时显示延迟）
    if (!barsVisible) {
        Surface(
            color = Color(0x660B1020), shape = RoundedCornerShape(20.dp),
            modifier = Modifier.align(Alignment.TopStart).statusBarsPadding().padding(10.dp).clickable { barsVisible = true },
        ) {
            Row(Modifier.padding(horizontal = 12.dp, vertical = 7.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.Tune, contentDescription = "显示工具栏", tint = Color.White, modifier = Modifier.size(16.dp))
                if (state.latencyMs >= 0) { Spacer(Modifier.width(6.dp)); Text("${state.latencyMs}ms", color = latencyColor(state.latencyMs), fontSize = 11.sp) }
            }
        }
    }

    // 顶部：返回 + 连接态(含延迟) + 设置 + 断开
    if (barsVisible) {
        Row(
            Modifier.align(Alignment.TopStart).fillMaxWidth().statusBarsPadding().padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Surface(color = Color(0x990B1020), shape = CircleShape) {
                IconButton(onClick = onExit) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "退出", tint = Color.White) }
            }
            Spacer(Modifier.width(8.dp))
            Surface(color = Color(0x990B1020), shape = RoundedCornerShape(20.dp)) {
                Row(Modifier.padding(horizontal = 12.dp, vertical = 7.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(7.dp).clip(CircleShape).background(Color(0xFF34C759)))
                    Spacer(Modifier.width(6.dp)); Text("已连接", color = Color.White, fontSize = 12.sp)
                    if (state.latencyMs >= 0) {
                        Spacer(Modifier.width(8.dp))
                        Text("· ${state.latencyMs}ms", color = latencyColor(state.latencyMs), fontSize = 12.sp)
                    }
                }
            }
            Spacer(Modifier.weight(1f))
            // 顶栏的"设置"齿轮已移除：右侧竖栏的「操作」按钮做同样的事，二者并排会重叠（用户反馈）。
        }
    }

    // 顶部居中临时提示
    toast?.let {
        Surface(color = Color(0xCC0B1020), shape = RoundedCornerShape(14.dp), modifier = Modifier.align(Alignment.TopCenter).statusBarsPadding().padding(top = 56.dp)) {
            Text(it, color = Color.White, fontSize = 13.sp, modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
        }
    }

    // 底部：展开面板 + 工具条
    if (barsVisible) {
        Column(
            Modifier.align(Alignment.BottomCenter).fillMaxWidth().imePadding().padding(start = 12.dp, end = 74.dp).padding(top = 6.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            when (panel) {
                "keyboard" -> RemoteKeyboard(
                    bg = panelBg, accent = accent,
                    onType = { viewModel.sendText(it) },
                    onKey = { viewModel.sendKey(it) },
                )
                "keys" -> KeysRow(bg = panelBg) { viewModel.sendKey(it) }
                "scroll" -> ScrollPanel(bg = panelBg, accent = accent) { dir -> viewModel.sendScroll(dir, 3) }
                "system" -> SystemPanel(bg = panelBg, accent = accent) { viewModel.sendSystem(it); panel = ""; toast = "已发送系统指令" }
            }
            // 贴底：面板直接到屏幕最底，下面不留空（抄 UU/图3）。
        }
    }

    // ── 右侧竖排工具栏（抄 UU：所有操作集中在右边黑色竖栏，可滚动）──
    if (barsVisible) {
        val trackpad = controlMode == "trackpad"
        Column(
            Modifier.align(Alignment.CenterEnd).fillMaxHeight().statusBarsPadding().navigationBarsPadding()
                .padding(top = 50.dp, bottom = 50.dp, end = 10.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterVertically),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            // 「操作」＝原设置入口（用户删了顶栏齿轮、保留这个）：打开右侧设置面板（Windows 快捷操作等）
            SideButton(Icons.Outlined.Tune, "操作", showSettings) { showSettings = true }
            SideButton(if (trackpad) Icons.Outlined.Mouse else Icons.Outlined.TouchApp, if (trackpad) "触控板" else "触屏", trackpad) {
                onModeChange(if (trackpad) "touch" else "trackpad")
                toast = if (trackpad) "已切到触屏（直接点目标）" else "已切到触控板（拖动移光标）"
            }
            SideButton(Icons.Outlined.AspectRatio, if (fillMode) "铺满" else "适应", false) {
                onFillModeChange(!fillMode)
                toast = if (!fillMode) "已切到「铺满」：填满手机屏幕，边缘可能裁切" else "已切到「适应」：完整显示整个桌面（可能有黑边）"
            }
            SideButton(Icons.Outlined.Keyboard, "键盘", panel == "keyboard") { panel = if (panel == "keyboard") "" else "keyboard" }
            SideButton(Icons.Outlined.SwapVert, "滚动", panel == "scroll") { panel = if (panel == "scroll") "" else "scroll" }
            SideButton(Icons.Outlined.PowerSettingsNew, "系统", panel == "system") { panel = if (panel == "system") "" else "system" }
            SideButton(Icons.Outlined.ContentPaste, "剪贴板") { viewModel.pushClipboard(); toast = "已发送本机剪贴板" }
            SideButton(Icons.Outlined.DesktopWindows, "显示桌面") { viewModel.sendKey("win+d"); toast = "已显示桌面" }
            SideButton(Icons.Outlined.GridView, "展示所有窗口") { viewModel.sendKey("win+tab"); toast = "已展示所有窗口" }
        }
    }

    // ── 悬浮虚拟鼠标（抄 UU）：触控板模式下显示。放在右侧工具栏之后渲染→层级在工具栏之上，小图标不会被工具栏盖住、可点。──
    if (controlMode == "trackpad") {
        FloatingMouse(
            accent = accent,
            cursor = cursor,
            onMove = { x, y -> viewModel.sendMouseMove(x, y) },
            onLeft = { val (x, y) = (cursor.value.x * 1000).toInt().coerceIn(0, 1000) to (cursor.value.y * 1000).toInt().coerceIn(0, 1000); viewModel.sendClickAt("left_click", x, y) },
            onRight = { val (x, y) = (cursor.value.x * 1000).toInt().coerceIn(0, 1000) to (cursor.value.y * 1000).toInt().coerceIn(0, 1000); viewModel.sendClickAt("right_click", x, y) },
            onScroll = { dir -> viewModel.sendScroll(dir, 3) },
        )
    }

    // ── 设置面板（对标 UU 远程的右侧设置）──
    if (showSettings) {
        // 半透明遮罩，点空白处关闭
        Box(Modifier.fillMaxSize().background(Color(0x80000000)).pointerInput(Unit) { detectTapGestures { showSettings = false; settingsPage = "main" } })
        // ── UU 风格右侧设置抽屉（整套照抄 UU 的控制面板）──
        Surface(
            color = Color(0xFF1B2230),
            modifier = Modifier.align(Alignment.CenterEnd).fillMaxHeight().width(300.dp).statusBarsPadding().navigationBarsPadding(),
        ) {
            Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 16.dp)) {
              when (settingsPage) {
                "security" -> {
                    SettingsSubHeader("安全") { settingsPage = "main" }
                    SettingSwitchRow("指纹验证", "开启后可更好地保护密码信息，解锁更安全", secFingerprint) { secFingerprint = it }
                    SettingSwitchRow("自动解锁被控端", "记住 Windows 开机密码，连接后自动登录系统", secAutoUnlock) { secAutoUnlock = it }
                    SettingSwitchRow("远程结束后被控端锁屏", "断开远程后自动锁定被控电脑", secLockOnEnd) { secLockOnEnd = it }
                    SettingSwitchRow("被控端静音运行", "将被控端设为静音，控制端仍可听声音", secMute) { secMute = it }
                    SettingSwitchRow("被控端防窥模式", "被控端本机黑屏，只有你这边能看到画面", secAntiPeek) { secAntiPeek = it }
                    Spacer(Modifier.height(10.dp))
                    Text("以上为被控端电脑设置，需被控端（桌面端）支持后生效；密码仅本地加密存储、不上传。", color = Color(0x80FFFFFF), fontSize = 11.sp, lineHeight = 16.sp)
                }
                "winops" -> {
                    SettingsSubHeader("Windows 快捷操作") { settingsPage = "main" }
                    listOf("锁屏" to "win+l", "显示桌面" to "win+d", "文件管理器" to "win+e", "运行" to "win+r", "任务管理器" to "ctrl+shift+escape", "截图" to "win+shift+s", "系统设置" to "win+i", "切换窗口" to "win+tab").chunked(2).forEach { pair ->
                        Row(Modifier.fillMaxWidth().padding(bottom = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            pair.forEach { (label, key) ->
                                Surface(color = Color(0x14FFFFFF), shape = RoundedCornerShape(12.dp), modifier = Modifier.weight(1f).clickable { viewModel.sendKey(key); toast = "已发送：$label" }) {
                                    Box(Modifier.fillMaxWidth().padding(vertical = 14.dp), contentAlignment = Alignment.Center) { Text(label, color = Color.White, fontSize = 14.sp) }
                                }
                            }
                            if (pair.size == 1) Spacer(Modifier.weight(1f))
                        }
                    }
                }
                else -> {
                // 顶部四个大图标：退出远控 / 旋转屏幕 / 声音 / 小窗模式
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    UuTopBtn(Icons.AutoMirrored.Outlined.ExitToApp, "退出远控", false, accent, Modifier.weight(1f)) { showSettings = false; onExit() }
                    UuTopBtn(Icons.Outlined.ScreenRotation, "旋转屏幕", rotated, accent, Modifier.weight(1f)) {
                        val act = context as? Activity
                        rotated = !rotated
                        act?.requestedOrientation = if (rotated) ActivityInfo.SCREEN_ORIENTATION_REVERSE_LANDSCAPE else ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
                    }
                    UuTopBtn(Icons.AutoMirrored.Outlined.VolumeUp, "声音", state.audioOn, accent, Modifier.weight(1f)) { viewModel.setRemoteAudio(!state.audioOn) }
                    UuTopBtn(Icons.Outlined.PictureInPictureAlt, "小窗模式", false, accent, Modifier.weight(1f)) { showSettings = false; enterPip(context) }
                }
                Spacer(Modifier.height(22.dp))
                Text("操作", color = Color(0xB3FFFFFF), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(4.dp))
                SettingSwitchRow("启用按键映射", "把屏幕按键映射到键鼠（游戏用）", keyMapping) { keyMapping = it }
                Spacer(Modifier.height(10.dp))
                UuSegment("屏幕触控", "鼠标指针", controlMode == "touch", accent) { left -> onModeChange(if (left) "touch" else "trackpad") }
                Spacer(Modifier.height(8.dp))
                SettingSwitchRow("平板触控模式", "支持多点触控，建议玩原神、杀戮尖塔等游戏触屏版时开启", tabletTouch) { tabletTouch = it }
                SettingSwitchRow("虚拟鼠标", "显示可拖动的虚拟光标，适合精细操作", controlMode == "trackpad") { on -> onModeChange(if (on) "trackpad" else "touch") }
                Spacer(Modifier.height(20.dp))
                Text("画面与画质", color = Color(0xB3FFFFFF), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
                    SettingChoice("铺满", "占满 · 裁边", fillMode, accent, Modifier.weight(1f)) { onFillModeChange(true) }
                    SettingChoice("适应", "完整 · 留边", !fillMode, accent, Modifier.weight(1f)) { onFillModeChange(false) }
                }
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SettingChoice("流畅", "省流量", state.quality == 0, accent, Modifier.weight(1f)) { viewModel.setRemoteQuality(0) }
                    SettingChoice("高清", "均衡", state.quality == 1, accent, Modifier.weight(1f)) { viewModel.setRemoteQuality(1) }
                    SettingChoice("极清", "最清晰", state.quality == 2, accent, Modifier.weight(1f)) { viewModel.setRemoteQuality(2) }
                }
                SettingSwitchRow("安全中转 (Beta)", "强制经 TURN 中转，不暴露直连 IP", state.relayOn) { viewModel.setRemoteRelay(it) }
                SettingSwitchRow("自动隐藏工具栏", "无操作 3.5 秒自动收起，点左上角手柄唤出", autoHide) { autoHide = it; if (!it) barsVisible = true }
                SettingInfoRow(Icons.Outlined.NetworkCheck, "网络延迟", if (state.latencyMs >= 0) "${state.latencyMs} ms" else "测量中…", latencyColor(state.latencyMs))
                SettingActionRow(Icons.Outlined.HelpOutline, "操控指南", "查看手势说明") { showSettings = false; showGuide = true }
                Spacer(Modifier.height(20.dp))
                Text("更多设置", color = Color(0xB3FFFFFF), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(8.dp))
                SettingActionRow(Icons.Outlined.Security, "安全", "指纹 / 锁屏 / 静音 / 防窥") { settingsPage = "security" }
                SettingActionRow(Icons.Outlined.Apps, "Windows 快捷操作", "锁屏 / 任务管理器 / 截图等") { settingsPage = "winops" }
                Spacer(Modifier.height(24.dp))
                }
              }
            }
        }
    }

    // ── 操控指南 ──
    if (showGuide) {
        ModalBottomSheet(onDismissRequest = { showGuide = false }, containerColor = Color(0xFF161E2E)) {
            Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp).padding(bottom = 30.dp)) {
                Text("操控指南", color = Color.White, fontSize = 17.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(14.dp))
                GuideLine("单击", "轻点屏幕 = 鼠标左键单击")
                GuideLine("双击", "快速点两下 = 双击")
                GuideLine("长按", "按住不放 = 鼠标右键")
                GuideLine("触屏 / 触控板", "触屏=直接点目标；触控板=拖动相对移光标，适合精细操作")
                GuideLine("滚动", "用工具栏「滚动」面板上下左右滚动")
                GuideLine("键盘 / 快捷键", "「键盘」输入文字回车；「快捷键」发 Esc / Tab / 方向键 / Ctrl+C 等")
                GuideLine("画面模式", "铺满=占满全屏（裁边）；适应=完整显示（留边）")
            }
        }
    }
}

@Composable
private fun SettingChoice(title: String, sub: String, active: Boolean, accent: Color, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Surface(
        color = if (active) accent.copy(alpha = 0.16f) else Color(0x14FFFFFF),
        shape = RoundedCornerShape(14.dp),
        border = if (active) androidx.compose.foundation.BorderStroke(1.5.dp, accent) else null,
        modifier = modifier.clickable(onClick = onClick),
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
            Text(title, color = if (active) accent else Color.White, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(3.dp))
            Text(sub, color = Color(0x99FFFFFF), fontSize = 11.sp)
        }
    }
}

/** UU 顶部大图标按钮（图标在上、文字在下，选中态填充强调色）。 */
@Composable
private fun UuTopBtn(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, active: Boolean, accent: Color, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Column(
        modifier = modifier.clip(RoundedCornerShape(14.dp)).background(if (active) accent else Color(0x1FFFFFFF)).clickable(onClick = onClick).padding(vertical = 14.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(icon, contentDescription = label, tint = Color.White, modifier = Modifier.size(26.dp))
        Spacer(Modifier.height(8.dp))
        Text(label, color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Medium, maxLines = 1)
    }
}

/** UU 二选一分段控件（如 屏幕触控 / 鼠标指针）。 */
@Composable
private fun UuSegment(left: String, right: String, leftActive: Boolean, accent: Color, onSelect: (Boolean) -> Unit) {
    Surface(color = Color(0x1FFFFFFF), shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(4.dp)) {
            SegTab(left, leftActive, accent, Modifier.weight(1f)) { onSelect(true) }
            SegTab(right, !leftActive, accent, Modifier.weight(1f)) { onSelect(false) }
        }
    }
}

@Composable
private fun SegTab(label: String, active: Boolean, accent: Color, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Box(
        modifier = modifier.clip(RoundedCornerShape(9.dp)).background(if (active) accent else Color.Transparent).clickable(onClick = onClick).padding(vertical = 11.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, color = Color.White, fontSize = 14.sp, fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal)
    }
}

@Composable
private fun SettingsSubHeader(title: String, onBack: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(bottom = 14.dp), verticalAlignment = Alignment.CenterVertically) {
        Surface(color = Color(0x1FFFFFFF), shape = CircleShape, modifier = Modifier.clickable(onClick = onBack)) {
            Box(Modifier.size(34.dp), contentAlignment = Alignment.Center) { Text("←", color = Color.White, fontSize = 18.sp) }
        }
        Spacer(Modifier.width(12.dp))
        Text(title, color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun SettingActionRow(icon: androidx.compose.ui.graphics.vector.ImageVector, title: String, value: String, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable(onClick = onClick).padding(vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, contentDescription = null, tint = Color(0xCCFFFFFF), modifier = Modifier.size(20.dp))
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, color = Color.White, fontSize = 15.sp, maxLines = 1)
            if (value.isNotEmpty()) {
                Spacer(Modifier.height(2.dp))
                Text(value, color = Color(0x99FFFFFF), fontSize = 12.sp, maxLines = 1)
            }
        }
        Spacer(Modifier.width(6.dp))
        Icon(Icons.Outlined.ChevronRight, contentDescription = null, tint = Color(0x80FFFFFF), modifier = Modifier.size(18.dp))
    }
}

@Composable
private fun SettingSwitchRow(title: String, sub: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, color = Color.White, fontSize = 15.sp)
            Spacer(Modifier.height(2.dp))
            Text(sub, color = Color(0x99FFFFFF), fontSize = 11.sp)
        }
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

@Composable
private fun SettingInfoRow(icon: androidx.compose.ui.graphics.vector.ImageVector, title: String, value: String, valueColor: Color) {
    Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, contentDescription = null, tint = Color(0xCCFFFFFF), modifier = Modifier.size(20.dp))
        Spacer(Modifier.width(12.dp))
        Text(title, color = Color.White, fontSize = 15.sp, modifier = Modifier.weight(1f))
        Text(value, color = valueColor, fontSize = 13.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun GuideLine(label: String, desc: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 7.dp)) {
        Text(label, color = Color(0xFFEF3E36), fontSize = 13.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.width(96.dp))
        Spacer(Modifier.width(10.dp))
        Text(desc, color = Color(0xCCFFFFFF), fontSize = 13.sp, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun SideButton(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, active: Boolean = false, onClick: () -> Unit) {
    Surface(color = if (active) Color(0xFFEF3E36) else Color(0xCC1F1F24), shape = RoundedCornerShape(12.dp), modifier = Modifier.width(52.dp).clickable { onClick() }) {
        Column(Modifier.padding(vertical = 7.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(icon, contentDescription = label, tint = Color.White, modifier = Modifier.size(19.dp))
            Spacer(Modifier.height(2.dp))
            Text(label, color = Color.White, fontSize = 9.sp, maxLines = 1)
        }
    }
}

/** UU 风格键盘：三标签——输入法（手机输入法打字）/ 快捷键 / 电脑键盘（整套硬件键）。 */
@Composable
private fun RemoteKeyboard(
    bg: Color, accent: Color,
    onType: (String) -> Unit,
    onKey: (String) -> Unit,
) {
    var tab by remember { mutableStateOf(0) }
    var imeText by remember { mutableStateOf("") }
    val focus = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current
    fun summon() { try { focus.requestFocus(); keyboard?.show() } catch (_: Exception) {} }
    LaunchedEffect(tab) { if (tab == 0) summon() }
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        Surface(color = bg, shape = RoundedCornerShape(12.dp)) {
            Row(Modifier.padding(4.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                KbTab("输入法", tab == 0, accent) { tab = 0; summon() }   // 再点也重新唤起键盘
                KbTab("快捷键", tab == 1, accent) { tab = 1 }
                KbTab("电脑键盘", tab == 2, accent) { tab = 2 }
            }
        }
        // 输入法：只放一个 1dp 不可见捕获框（手机键盘打的字实时发到电脑），tab 紧贴系统键盘、中间不留任何东西。
        if (tab == 0) {
            BasicTextField(
                value = imeText,
                onValueChange = { neu ->
                    if (neu != imeText) {
                        var cp = 0
                        val m = minOf(imeText.length, neu.length)
                        while (cp < m && imeText[cp] == neu[cp]) cp++
                        repeat(imeText.length - cp) { onKey("backspace") }
                        if (neu.length > cp) onType(neu.substring(cp))
                        imeText = neu
                    }
                },
                modifier = Modifier.size(1.dp).alpha(0f).focusRequester(focus),
                singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { onKey("enter") }),
            )
        }
        when (tab) {
            1 -> KeysRow(bg = bg) { onKey(it) }
            2 -> PcKeyboard(bg = bg, accent = accent) { onKey(it) }
        }
    }
}

@Composable
private fun KbTab(label: String, active: Boolean, accent: Color, onClick: () -> Unit) {
    Surface(color = if (active) accent else Color.Transparent, shape = RoundedCornerShape(8.dp), modifier = Modifier.clickable { onClick() }) {
        Text(label, color = if (active) Color.White else Color(0xCCFFFFFF), fontSize = 13.sp,
            fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal,
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 7.dp))
    }
}

/** 整套电脑键盘：功能键 + 数字 + 字母 + 方向键；Ctrl/Alt/Shift/Win 为粘滞修饰键，点字母即发组合键。 */
@Composable
private fun PcKeyboard(bg: Color, accent: Color, onKey: (String) -> Unit) {
    var ctrl by remember { mutableStateOf(false) }
    var alt by remember { mutableStateOf(false) }
    var shift by remember { mutableStateOf(false) }
    var page by remember { mutableStateOf(0) }
    var drag by remember { mutableFloatStateOf(0f) }
    // 点 Ctrl/Alt/Shift 先"按住"（高亮），再点普通键发组合键（如 Ctrl+C），发完自动松开；无修饰键时直接发。
    fun fire(send: String) {
        val mods = mutableListOf<String>()
        if (ctrl) mods += "ctrl"; if (shift) mods += "shift"; if (alt) mods += "alt"
        onKey((mods + send).joinToString("+"))
        ctrl = false; alt = false; shift = false
    }
    Surface(color = bg, shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
        Column(
            Modifier.padding(6.dp).pointerInput(Unit) {
                detectHorizontalDragGestures(
                    onDragEnd = { if (drag < -55f) page = 1 else if (drag > 55f) page = 0; drag = 0f },
                    onDragCancel = { drag = 0f },
                ) { _, dx -> drag += dx }
            },
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            val rows = if (page == 0) UU_KB_PAGE1 else UU_KB_PAGE2
            rows.forEach { row ->
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    var sum = 0f
                    row.forEach { k ->
                        sum += k.w
                        if (k.mod) {
                            val act = (k.send == "ctrl" && ctrl) || (k.send == "alt" && alt) || (k.send == "shift" && shift)
                            KKey(k.disp, Modifier.weight(k.w), active = act, accent = accent) {
                                when (k.send) { "ctrl" -> ctrl = !ctrl; "alt" -> alt = !alt; "shift" -> shift = !shift }
                            }
                        } else {
                            val label = if (shift && k.shift != null) k.shift!! else k.disp
                            KKey(label, Modifier.weight(k.w), accent = accent) { fire(k.send) }
                        }
                    }
                    // 第 1 页保留右侧留白对齐；第 2 页（功能键/导航键）铺满整行，两边一样齐、不留空。
                    if (page == 0 && sum < 9.99f) Spacer(Modifier.weight(10f - sum))
                }
            }
            // 两页小圆点（点或左右滑切换，抄 UU 分页）
            Row(Modifier.fillMaxWidth().padding(top = 3.dp), horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(7.dp).clip(CircleShape).background(if (page == 0) accent else Color(0x44FFFFFF)).clickable { page = 0 })
                Spacer(Modifier.width(7.dp))
                Box(Modifier.size(7.dp).clip(CircleShape).background(if (page == 1) accent else Color(0x44FFFFFF)).clickable { page = 1 })
            }
        }
    }
}

// ── UU 电脑键盘布局（直接搬自 UU 的 assets/terminal_keyboard_page1.json / page2.json）──
private class PcKeyDef(val disp: String, val shift: String?, val send: String, val w: Float = 1f, val mod: Boolean = false)
private val UU_KB_PAGE1: List<List<PcKeyDef>> = listOf(
    listOf(PcKeyDef("1", "!", "1"), PcKeyDef("2", "@", "2"), PcKeyDef("3", "#", "3"), PcKeyDef("4", "\$", "4"), PcKeyDef("5", "%", "5"), PcKeyDef("6", "^", "6"), PcKeyDef("7", "&", "7"), PcKeyDef("8", "*", "8"), PcKeyDef("9", "(", "9"), PcKeyDef("0", ")", "0")),
    listOf(PcKeyDef("Q", null, "q"), PcKeyDef("W", null, "w"), PcKeyDef("E", null, "e"), PcKeyDef("R", null, "r"), PcKeyDef("T", null, "t"), PcKeyDef("Y", null, "y"), PcKeyDef("U", null, "u"), PcKeyDef("I", null, "i"), PcKeyDef("O", null, "o"), PcKeyDef("P", null, "p")),
    listOf(PcKeyDef("A", null, "a"), PcKeyDef("S", null, "s"), PcKeyDef("D", null, "d"), PcKeyDef("F", null, "f"), PcKeyDef("G", null, "g"), PcKeyDef("H", null, "h"), PcKeyDef("J", null, "j"), PcKeyDef("K", null, "k"), PcKeyDef("L", null, "l"), PcKeyDef("[", "{", "[")),
    listOf(PcKeyDef("Shift", null, "shift", 1f, true), PcKeyDef("Z", null, "z"), PcKeyDef("X", null, "x"), PcKeyDef("C", null, "c"), PcKeyDef("V", null, "v"), PcKeyDef("B", null, "b"), PcKeyDef("N", null, "n"), PcKeyDef("M", null, "m"), PcKeyDef("\u232b", null, "backspace"), PcKeyDef("]", "}", "]")),
    listOf(PcKeyDef("Ctrl", null, "ctrl", 1f, true), PcKeyDef("Alt", null, "alt", 1f, true), PcKeyDef("Tab", null, "tab"), PcKeyDef("Space", null, "space", 2f), PcKeyDef("Enter", null, "enter"), PcKeyDef("\u2190", null, "left"), PcKeyDef("\u2191", null, "up"), PcKeyDef("\u2193", null, "down"), PcKeyDef("\u2192", null, "right")),
)
private val UU_KB_PAGE2: List<List<PcKeyDef>> = listOf(
    listOf(PcKeyDef("Esc", null, "escape"), PcKeyDef("`", "~", "`"), PcKeyDef("-", "_", "-"), PcKeyDef("=", "+", "="), PcKeyDef("\\", "|", "\\"), PcKeyDef(";", ":", ";"), PcKeyDef("'", "\"", "'"), PcKeyDef(",", "<", ","), PcKeyDef(".", ">", "."), PcKeyDef("/", "?", "/")),
    listOf(PcKeyDef("F1", null, "f1"), PcKeyDef("F2", null, "f2"), PcKeyDef("F3", null, "f3"), PcKeyDef("F4", null, "f4"), PcKeyDef("F5", null, "f5"), PcKeyDef("F6", null, "f6"), PcKeyDef("F7", null, "f7"), PcKeyDef("F8", null, "f8"), PcKeyDef("F9", null, "f9"), PcKeyDef("F10", null, "f10"), PcKeyDef("F11", null, "f11"), PcKeyDef("F12", null, "f12")),
    listOf(PcKeyDef("PrtSc", null, "printscreen"), PcKeyDef("ScrLk", null, "scrolllock"), PcKeyDef("Pause", null, "pause"), PcKeyDef("Ins", null, "insert"), PcKeyDef("Home", null, "home"), PcKeyDef("PgUp", null, "pageup"), PcKeyDef("Del", null, "delete"), PcKeyDef("End", null, "end"), PcKeyDef("PgDn", null, "pagedown")),
)

@Composable
private fun KKey(label: String, modifier: Modifier = Modifier, active: Boolean = false, accent: Color = Color(0xFFEF3E36), onClick: () -> Unit) {
    Surface(
        color = if (active) accent else Color(0x24FFFFFF),
        shape = RoundedCornerShape(8.dp),
        modifier = modifier.height(23.dp).clickable { onClick() },
    ) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text(label, color = Color.White, fontSize = 10.sp, fontWeight = FontWeight.Medium, maxLines = 1)
        }
    }
}

@Composable
private fun MouseBtn(label: String, accent: Color, onClick: () -> Unit) {
    Surface(color = Color(0xE6161E2E), shape = RoundedCornerShape(16.dp), modifier = Modifier.width(118.dp).clickable(onClick = onClick)) {
        Box(Modifier.fillMaxWidth().padding(vertical = 13.dp), contentAlignment = Alignment.Center) {
            Text(label, color = Color.White, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}

/**
 * 悬浮可拖动虚拟鼠标（抄 UU 远程的"鼠标形状"控件）。
 * 收起态：右下角一个可拖动的小鼠标图标；点一下展开。
 * 展开态：一个鼠标外形——整块拖动=移光标；顶部左半=左键、右半=右键；中间滚轮=上下滚动；右上角透明 ✕ 收起。
 * 点击/滚动按"落点位置"路由（视觉层不拦截手势），避免按钮与拖动冲突。
 */
@Composable
private fun BoxScope.FloatingMouse(
    accent: Color,
    cursor: androidx.compose.runtime.MutableState<androidx.compose.ui.geometry.Offset>,
    onMove: (Int, Int) -> Unit,
    onLeft: () -> Unit,
    onRight: () -> Unit,
    onScroll: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    var offX by remember { mutableStateOf(-104f) }   // px，相对 BottomEnd 向左（避开右侧竖排工具栏，否则小图标压在按钮下点不到）
    var offY by remember { mutableStateOf(-360f) }   // px，相对 BottomEnd 向上

    if (!expanded) {
        Surface(
            color = Color(0xCC1B2230), shape = CircleShape,
            modifier = Modifier.align(Alignment.BottomEnd)
                .offset { IntOffset(offX.toInt(), offY.toInt()) }
                .size(48.dp)
                .pointerInput(Unit) { detectDragGestures { _, drag -> offX += drag.x; offY += drag.y } }
                .pointerInput(Unit) { detectTapGestures(onTap = { expanded = true }) },
        ) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Icon(Icons.Outlined.Mouse, contentDescription = "虚拟鼠标", tint = Color.White, modifier = Modifier.size(24.dp))
            }
        }
    } else {
        val shape = RoundedCornerShape(topStart = 54.dp, topEnd = 54.dp, bottomStart = 26.dp, bottomEnd = 26.dp)
        val bodyBrush = Brush.verticalGradient(listOf(Color(0xF21E2D48), Color(0xF20B1322)))
        Box(modifier = Modifier.align(Alignment.BottomEnd).offset { IntOffset(offX.toInt(), offY.toInt()) }) {
            // 鼠标外形：顶部左半=左键、右半=右键（纯点击，绝不滑动）；中间滚轮点上/下半滚动；底部手柄拖动=挪窗。
            // 移动光标：在「屏幕」上拖（默认虚拟鼠标模式），白箭头跟着走——不在鼠标身上拖，从根上避免点击和拖动打架。
            Box(Modifier.size(116.dp, 134.dp).clip(shape).background(bodyBrush).border(1.dp, Color(0x33FFFFFF), shape)) {
                Column(Modifier.fillMaxSize()) {
                    Row(Modifier.fillMaxWidth().weight(0.56f)) {
                        Box(Modifier.weight(1f).fillMaxHeight().clickable { onLeft() }, contentAlignment = Alignment.Center) {
                            Text("左键", color = Color(0xE6FFFFFF), fontSize = 12.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(top = 18.dp))
                        }
                        Box(Modifier.width(1.dp).fillMaxHeight(0.62f).align(Alignment.CenterVertically).background(Color(0x2EFFFFFF)))
                        Box(Modifier.weight(1f).fillMaxHeight().clickable { onRight() }, contentAlignment = Alignment.Center) {
                            Text("右键", color = Color(0xE6FFFFFF), fontSize = 12.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(top = 18.dp))
                        }
                    }
                    Box(Modifier.fillMaxWidth().padding(horizontal = 18.dp).height(1.dp).background(Color(0x22FFFFFF)))
                    Box(
                        Modifier.fillMaxWidth().weight(0.44f)
                            .pointerInput(Unit) { detectDragGestures { _, drag -> offX += drag.x; offY += drag.y } },
                        contentAlignment = Alignment.Center,
                    ) { Box(Modifier.width(34.dp).height(5.dp).clip(RoundedCornerShape(3.dp)).background(Color(0x40FFFFFF))) }
                }
                // 中间滚轮：胶囊 + ▲▼，上半点=上滚、下半点=下滚（独立 clickable，z 序在上，不和左右键冲突）
                Column(
                    Modifier.align(Alignment.TopCenter).padding(top = 15.dp)
                        .size(28.dp, 50.dp).clip(RoundedCornerShape(14.dp))
                        .background(Brush.verticalGradient(listOf(Color(0x40FFFFFF), Color(0x14FFFFFF))))
                        .border(1.dp, Color(0x33FFFFFF), RoundedCornerShape(14.dp)),
                ) {
                    Box(Modifier.fillMaxWidth().weight(1f).clickable { onScroll("up") }, contentAlignment = Alignment.Center) {
                        Text("▲", color = Color(0xF2FFFFFF), fontSize = 10.sp)
                    }
                    Box(Modifier.fillMaxWidth().height(1.dp).background(Color(0x33FFFFFF)))
                    Box(Modifier.fillMaxWidth().weight(1f).clickable { onScroll("down") }, contentAlignment = Alignment.Center) {
                        Text("▼", color = Color(0xF2FFFFFF), fontSize = 10.sp)
                    }
                }
            }
            // 右上角透明 ✕：收起成小鼠标
            Box(
                Modifier.align(Alignment.TopEnd).offset(x = 9.dp, y = (-7).dp).size(24.dp)
                    .clip(CircleShape).background(Color(0x99000000)).clickable { expanded = false },
                contentAlignment = Alignment.Center,
            ) { Text("\u2715", color = Color.White, fontSize = 13.sp) }
        }
    }
}

@Composable
private fun ToolButton(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, active: Boolean, accent: Color, onClick: () -> Unit) {
    val tint = if (active) accent else Color.White
    Column(
        Modifier.clip(RoundedCornerShape(18.dp)).clickable(onClick = onClick).padding(horizontal = 14.dp, vertical = 8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(icon, contentDescription = label, tint = tint, modifier = Modifier.size(22.dp))
        Spacer(Modifier.height(3.dp))
        Text(label, color = tint, fontSize = 11.sp, fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal)
    }
}

@Composable
private fun ScrollPanel(bg: Color, accent: Color, onScroll: (String) -> Unit) {
    Surface(color = bg, shape = RoundedCornerShape(18.dp)) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 12.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text("滚动 · 点按或长按连续滚", color = Color(0xCCFFFFFF), fontSize = 12.sp)
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                ScrollBtn(Icons.AutoMirrored.Outlined.KeyboardArrowLeft, accent) { onScroll("left") }
                Column(verticalArrangement = Arrangement.spacedBy(10.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    ScrollBtn(Icons.Outlined.KeyboardArrowUp, accent) { onScroll("up") }
                    ScrollBtn(Icons.Outlined.KeyboardArrowDown, accent) { onScroll("down") }
                }
                ScrollBtn(Icons.AutoMirrored.Outlined.KeyboardArrowRight, accent) { onScroll("right") }
            }
        }
    }
}

@Composable
private fun ScrollBtn(icon: androidx.compose.ui.graphics.vector.ImageVector, accent: Color, onClick: () -> Unit) {
    Surface(color = Color(0x22FFFFFF), shape = CircleShape, modifier = Modifier.size(46.dp).clip(CircleShape).clickable(onClick = onClick)) {
        Box(contentAlignment = Alignment.Center) { Icon(icon, contentDescription = null, tint = Color.White, modifier = Modifier.size(26.dp)) }
    }
}

/** 输入法 tab：抄 UU/图三——直接调起手机系统键盘，打的字实时发到电脑（中英文都行）。
 *  按用户要求做成图三那样：不要大输入框、不要多余按钮，只留一条很细的可点提示；系统键盘的"发送"=回车。*/
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ImeInputPanel(bg: Color, accent: Color, onType: (String) -> Unit, onKey: (String) -> Unit) {
    var text by remember { mutableStateOf("") }
    val focus = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current
    fun summon() { try { focus.requestFocus(); keyboard?.show() } catch (_: Exception) {} }
    LaunchedEffect(Unit) { summon() }
    Box(Modifier.fillMaxWidth().clickable { summon() }) {
        // 不可见捕获框：手机键盘打的字实时发到电脑，但不显示难看的大输入框。
        BasicTextField(
            value = text,
            onValueChange = { neu ->
                if (neu != text) {
                    var cp = 0
                    val m = minOf(text.length, neu.length)
                    while (cp < m && text[cp] == neu[cp]) cp++
                    repeat(text.length - cp) { onKey("backspace") }
                    if (neu.length > cp) onType(neu.substring(cp))
                    text = neu
                }
            },
            modifier = Modifier.size(1.dp).alpha(0f).focusRequester(focus),
            singleLine = true,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onSend = { onKey("enter") }),
        )
        // 一条很细的可点提示（点=唤起手机键盘）；尽量矮，让 tab 紧贴系统键盘。
        Surface(color = Color(0x14FFFFFF), shape = RoundedCornerShape(8.dp), modifier = Modifier.fillMaxWidth()) {
            Row(Modifier.padding(horizontal = 12.dp, vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("⌨", fontSize = 11.sp)
                Spacer(Modifier.width(6.dp))
                Text("在手机键盘打字，实时发到电脑（点这唤起键盘）", color = Color(0x99FFFFFF), fontSize = 11.sp)
            }
        }
    }
}

@Composable
private fun ImeMiniKey(label: String, color: Color, onClick: () -> Unit) {
    Surface(color = color, shape = RoundedCornerShape(10.dp), modifier = Modifier.clickable(onClick = onClick)) {
        Text(label, color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(horizontal = 14.dp, vertical = 8.dp))
    }
}

/** 快捷键 tab：抄 UU——内置常用快捷键 + 支持「添加自定义」（如 ctrl+shift+s），自定义项可删除、跨标签保留。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun KeysRow(bg: Color, onKey: (String) -> Unit) {
    // 抄 UU 快捷键：卡片网格，按键在上、中文说明在下
    val keys = listOf(
        Triple("Ctrl+C", "ctrl+c", "复制"), Triple("Ctrl+V", "ctrl+v", "粘贴"), Triple("Ctrl+X", "ctrl+x", "剪切"), Triple("Ctrl+A", "ctrl+a", "全选"),
        Triple("Ctrl+Z", "ctrl+z", "撤销"), Triple("Ctrl+S", "ctrl+s", "保存"), Triple("Win+D", "win+d", "显示桌面"), Triple("Win+L", "win+l", "锁屏"),
        Triple("Win+E", "win+e", "文件管理"), Triple("Win+X", "win+x", "快捷菜单"), Triple("Win+Tab", "win+tab", "切换窗口"), Triple("Win", "win", "开始菜单"),
        Triple("Caps", "capslock", "大小写"), Triple("Tab", "tab", "制表"), Triple("Esc", "escape", "退出"), Triple("\u23ce", "enter", "回车"),
    )
    Surface(color = bg, shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(horizontal = 6.dp, vertical = 5.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            keys.chunked(6).forEach { row ->
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(3.dp)) {
                    row.forEach { (disp, send, label) ->
                        Surface(color = Color(0x1FFFFFFF), shape = RoundedCornerShape(7.dp), modifier = Modifier.weight(1f).clickable { onKey(send) }) {
                            Column(Modifier.padding(vertical = 2.dp, horizontal = 2.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                                Text(disp, color = Color.White, fontSize = 9.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
                                Text(label, color = Color(0x99FFFFFF), fontSize = 6.sp, maxLines = 1)
                            }
                        }
                    }
                    repeat(6 - row.size) { Spacer(Modifier.weight(1f)) }
                }
            }
        }
    }
}

@Composable
private fun SystemPanel(bg: Color, accent: Color, onCmd: (String) -> Unit) {
    // (显示, cmd, 是否危险)
    val items = listOf(
        Triple("锁屏", "lock", false), Triple("显示桌面", "show_desktop", false), Triple("任务管理器", "task_manager", false),
        Triple("重启", "reboot", true), Triple("关机", "shutdown", true),
    )
    Surface(color = bg, shape = RoundedCornerShape(18.dp), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 10.dp, vertical = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            items.forEach { (label, cmd, danger) ->
                Surface(
                    color = if (danger) accent.copy(alpha = 0.85f) else Color(0x1FFFFFFF),
                    shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.clickable { onCmd(cmd) },
                ) {
                    Text(label, color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(horizontal = 14.dp, vertical = 9.dp))
                }
            }
        }
    }
}

@Composable
private fun DevicePicker(
    devices: List<RemoteDevice>,
    relayOn: Boolean,
    onToggleRelay: () -> Unit,
    onPick: (RemoteDevice) -> Unit,
    onRetry: () -> Unit,
) {
    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { Spacer(Modifier.height(4.dp)) }
        item {
            Surface(
                color = MaterialTheme.colorScheme.surface,
                shape = RoundedCornerShape(14.dp),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Row(Modifier.fillMaxWidth().padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("中继模式", fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                        Spacer(Modifier.height(3.dp))
                        Text(
                            "校园网/公司网连不上、一直「正在协商」时打开：画面经服务器中转，更易连通（画质略降）。",
                            fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Spacer(Modifier.width(12.dp))
                    Box(
                        Modifier.width(46.dp).height(26.dp).clip(CircleShape)
                            .background(if (relayOn) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline.copy(alpha = 0.3f))
                            .clickable { onToggleRelay() },
                        contentAlignment = if (relayOn) Alignment.CenterEnd else Alignment.CenterStart,
                    ) {
                        Box(Modifier.padding(3.dp).size(20.dp).clip(CircleShape).background(Color.White))
                    }
                }
            }
        }
        if (devices.isEmpty()) {
            item {
                Column(
                    Modifier.fillMaxWidth().padding(top = 72.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    HashMascot(Modifier.size(88.dp))
                    Spacer(Modifier.height(16.dp))
                    Text("暂无在线设备", fontWeight = FontWeight.Bold, fontSize = 17.sp, color = MaterialTheme.colorScheme.onSurface)
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "在电脑端登录同一账号、打开客户端即自动上线可控",
                        fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(18.dp))
                    OutlinedButton(onClick = onRetry) { Text("重新检测") }
                }
            }
        } else {
            item { DeviceSectionHeader("在线设备", devices.size) }
            items(devices) { d -> DeviceCard(d, online = true) { onPick(d) } }
        }
        item { Spacer(Modifier.height(12.dp)) }
    }
}

@Composable
private fun DeviceSectionHeader(label: String, count: Int) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.padding(start = 2.dp, top = 8.dp, bottom = 2.dp),
    ) {
        Text(label, fontWeight = FontWeight.SemiBold, fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurface)
        Spacer(Modifier.width(6.dp))
        Text(count.toString(), fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

private val deviceGradients = listOf(
    listOf(Color(0xFF4F86C6), Color(0xFFA9CCE8)),  // 蓝天白云
    listOf(Color(0xFF3A4D7A), Color(0xFF7C8AC0)),  // 暮蓝
    listOf(Color(0xFF2E7D6B), Color(0xFF8FD3C2)),  // 青绿
    listOf(Color(0xFF8B5E83), Color(0xFFCBA3C5)),  // 暮紫
)

@Composable
private fun DeviceCard(d: RemoteDevice, online: Boolean, onClick: () -> Unit) {
    val g = deviceGradients[d.name.hashCode().absoluteValue % deviceGradients.size]
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(18.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)),
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
    ) {
        Row(Modifier.fillMaxWidth().padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
            // 壁纸缩略图 + 在线徽标（仿 UU 远程）
            Box(
                Modifier.size(width = 112.dp, height = 72.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(Brush.linearGradient(g)),
            ) {
                if (online) {
                    Surface(
                        color = Color(0xE6101012), shape = RoundedCornerShape(10.dp),
                        modifier = Modifier.padding(8.dp),
                    ) {
                        Row(
                            Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Box(Modifier.size(7.dp).clip(CircleShape).background(Color(0xFF34C759)))
                            Spacer(Modifier.width(5.dp))
                            Text("在线", color = Color.White, fontSize = 11.sp)
                        }
                    }
                }
            }
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(d.name.ifBlank { "电脑" }, fontWeight = FontWeight.Bold, fontSize = 18.sp, color = MaterialTheme.colorScheme.onSurface)
                Spacer(Modifier.height(5.dp))
                Icon(
                    Icons.Outlined.GridView, contentDescription = d.platform.ifBlank { "客户端" },
                    tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(22.dp),
                )
            }
            Icon(Icons.Outlined.ChevronRight, contentDescription = "连接", tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun CenterInfo(
    title: String,
    subtitle: String = "",
    loading: Boolean = false,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
) {
    Column(
        Modifier.fillMaxSize().padding(32.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        if (loading) { CircularProgressIndicator(); Spacer(Modifier.height(16.dp)) }
        Text(title, style = MaterialTheme.typography.titleMedium)
        if (subtitle.isNotBlank()) {
            Spacer(Modifier.height(8.dp))
            Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (actionLabel != null && onAction != null) {
            Spacer(Modifier.height(20.dp))
            Button(onClick = onAction) { Text(actionLabel) }
        }
    }
}

// ── 触摸坐标归一化到 0..1000（相对视频内容区）──
// fill=true（铺满）：内容按 max 缩放、溢出裁边，可视框映射到内容子区；
// fill=false（适应）：内容按 min 缩放、居中留边，扣除黑边后映射。两式统一（铺满时 left/top 为负）。
private fun norm(tx: Float, ty: Float, boxW: Int, boxH: Int, frameW: Int, frameH: Int, fill: Boolean): Pair<Int, Int> {
    if (boxW <= 0 || boxH <= 0) return 0 to 0
    val nw = if (frameW > 0) frameW else boxW
    val nh = if (frameH > 0) frameH else boxH
    val sx = boxW.toFloat() / nw; val sy = boxH.toFloat() / nh
    val scale = if (fill) maxOf(sx, sy) else minOf(sx, sy)
    val cw = nw * scale; val ch = nh * scale
    val left = (boxW - cw) / 2f; val top = (boxH - ch) / 2f
    fun clamp(v: Float) = v.coerceIn(0f, 1000f)
    val x = clamp(((tx - left) / cw) * 1000f)
    val y = clamp(((ty - top) / ch) * 1000f)
    return x.toInt() to y.toInt()
}

private fun send(vm: RemoteControlViewModel, action: String, tx: Float, ty: Float, boxW: Int, boxH: Int, frameW: Int, frameH: Int, fill: Boolean) {
    val (x, y) = norm(tx, ty, boxW, boxH, frameW, frameH, fill)
    vm.sendInput(JSONObject().put("action", action).put("x", x).put("y", y))
}

// ── 小窗模式（PiP）：把远程画面缩成系统悬浮小窗，可边看边操作别的 App ──
private fun enterPip(context: Context) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
    try {
        context.findActivity()?.enterPictureInPictureMode(PictureInPictureParams.Builder().build())
    } catch (_: Exception) {}
}

private fun Context.findActivity(): Activity? {
    var c: Context = this
    while (c is ContextWrapper) {
        if (c is Activity) return c
        c = c.baseContext
    }
    return null
}
