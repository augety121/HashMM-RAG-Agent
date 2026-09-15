// 精准检查：只针对 lucide 图标——提取每个文件 import 的图标名，检查 JSX <Icon 用法是否都在 import 里
import fs from 'fs';
const files = fs.readdirSync('components').filter(f => f.endsWith('.tsx')).map(f => 'components/'+f);
let problems = 0;
// 已知 lucide 图标名集合（截至 0.383）——只检查这些已知图标名是否被用但没导入
const KNOWN = new Set(['ChevronDown','ChevronUp','ChevronRight','ChevronLeft','Search','Brain','Zap','Wrench','Network','FileText','Sparkles','Code2','Globe','Database','Bot','ShieldCheck','Quote','ListChecks','RefreshCw','Flag','MessageSquare','Target','Package','PenLine','Puzzle','TrendingUp','Archive','Lock','CheckCheck','Loader2','CheckCircle2','AlertCircle','Plus','Trash2','Star','FolderOpen','Terminal','BarChart3','Plug','X','Folder','Eraser','Play','HardDrive','ArrowLeft','Home','Cpu','Clock','Users','Download','Upload','Eye','PanelLeft','PanelLeftClose','Filter','Send','Image','FileCode','FileSpreadsheet','File','Check','Settings','Bell','User','LogOut','Copy','ExternalLink','Maximize2','Minimize2']);
for (const f of files) {
  const src = fs.readFileSync(f,'utf8');
  const m = src.match(/import\s*\{([^}]+)\}\s*from\s*["']lucide-react["']/);
  const imported = new Set(m ? m[1].split(',').map(x=>x.trim().split(' as ').pop().trim()) : []);
  // JSX 中 <Xxx 形式且名字在 KNOWN 图标集 → 必须 import
  const used = new Set([...src.matchAll(/<([A-Z][a-zA-Z0-9]+)[\s/>]/g)].map(x=>x[1]));
  for (const u of used) {
    if (KNOWN.has(u) && !imported.has(u)) {
      console.log(`✗ ${f}: <${u}> 是 lucide 图标但未 import`);
      problems++;
    }
  }
}
console.log(problems ? `\n发现 ${problems} 处图标缺失` : '✓ 所有 lucide 图标引用完整');
process.exit(problems ? 1 : 0);
