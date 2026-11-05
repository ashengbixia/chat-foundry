import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..') // 前端项目目录
const nativeRequire = createRequire(import.meta.url) // 加载现有测试依赖

export function createTypeScriptLoader(mocks = {}) { // 内存加载 TypeScript，并允许测试替换接口，不生成文件或发送请求
  const modules = new Map() // 每个加载器使用独立模块缓存
  function loadTypeScript(relativePath) { // 加载项目中的 TypeScript 模块
    const filename = resolve(projectRoot, relativePath) // 当前模块的绝对路径
    if (modules.has(filename)) return modules.get(filename).exports
    const code = ts.transpileModule(readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText // 编译为内存中可执行的代码
    const module = { exports: {} } // 当前模块导出对象
    modules.set(filename, module)
    const requireModule = (name) => { // 优先使用模拟接口，再解析正常导入
      if (Object.hasOwn(mocks, name)) return mocks[name]
      return name.startsWith('.') ? loadTypeScript(resolve(dirname(filename), `${name}.ts`)) : nativeRequire(name)
    }
    new Function('require', 'module', 'exports', code)(requireModule, module, module.exports)
    return module.exports
  }
  return loadTypeScript
}
