"""Regenerate PartScan.xcodeproj (dependency-free, Swift only) after adding source files to PartScan/."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
def identifier(label): return hashlib.sha1(label.encode()).hexdigest()[:24].upper()
def quote(value): return json.dumps(str(value))
def main():
    objects = []
    def obj(key, content): objects.append(f'\t\t{identifier(key)} = {{ {content} }};'); return identifier(key)
    source_files = sorted((ROOT/'PartScan').glob('*.swift'))
    resources = [ROOT/'PartScan'/'Assets.xcassets', ROOT/'PartScan'/'PrivacyInfo.xcprivacy']
    other = [ROOT/'PartScan'/'Info.plist']
    refs = []; builds = []; resource_builds = []
    for path in source_files+resources+other:
        relative=path.relative_to(ROOT).as_posix(); key='file:'+relative
        file_type={'.swift':'sourcecode.swift','.c':'sourcecode.c.c','.h':'sourcecode.c.h',
                   '.plist':'text.plist.xml','.xcprivacy':'text.plist.xml','.xcassets':'folder.assetcatalog'}[path.suffix]
        ref=obj(key,f'isa = PBXFileReference; lastKnownFileType = {file_type}; path = {quote(relative)}; sourceTree = SOURCE_ROOT;')
        refs.append(ref)
        if path in source_files or path in resources:
            build=obj('build:'+relative,f'isa = PBXBuildFile; fileRef = {ref};')
            (builds if path in source_files else resource_builds).append(build)
    product=obj('product','isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = PartScan.app; sourceTree = BUILT_PRODUCTS_DIR;')
    products=obj('products',f'isa = PBXGroup; children = ({product},); name = Products; sourceTree = "<group>";')
    group=obj('group',f'isa = PBXGroup; children = ({",".join(refs+[products])},); sourceTree = "<group>";')
    sources=obj('sources',f'isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = ({",".join(builds)},); runOnlyForDeploymentPostprocessing = 0;')
    resource_phase=obj('resources',f'isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = ({",".join(resource_builds)},); runOnlyForDeploymentPostprocessing = 0;')
    framework_phase=obj('frameworks','isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0;')
    common={'CLANG_ENABLE_MODULES':'YES','CLANG_ENABLE_OBJC_ARC':'YES','CLANG_C_LANGUAGE_STANDARD':'c99',
            'GCC_WARN_64_TO_32_BIT_CONVERSION':'YES','GCC_WARN_UNDECLARED_SELECTOR':'YES',
            'ENABLE_USER_SCRIPT_SANDBOXING':'YES','IPHONEOS_DEPLOYMENT_TARGET':'17.0','SDKROOT':'iphoneos'}
    target_common={'PRODUCT_NAME':'$(TARGET_NAME)','PRODUCT_BUNDLE_IDENTIFIER':'com.example.PartScan',
                   'SWIFT_VERSION':'5.0',
                   'SWIFT_STRICT_CONCURRENCY':'minimal','CODE_SIGN_STYLE':'Automatic','DEVELOPMENT_TEAM':'',
                   'INFOPLIST_FILE':'PartScan/Info.plist','GENERATE_INFOPLIST_FILE':'NO','TARGETED_DEVICE_FAMILY':'1',
                   'SUPPORTED_PLATFORMS':'iphoneos iphonesimulator','SUPPORTS_MACCATALYST':'NO',
                   'MARKETING_VERSION':'1.0.0','CURRENT_PROJECT_VERSION':'1',
                   'ASSETCATALOG_COMPILER_APPICON_NAME':'AppIcon','LD_RUNPATH_SEARCH_PATHS':'$(inherited) @executable_path/Frameworks'}
    configs={}
    for scope in ('project','target'):
        ids=[]
        for mode in ('Debug','Release'):
            settings=dict(common if scope=='project' else target_common)
            if scope=='project':
                settings.update({'GCC_OPTIMIZATION_LEVEL':'0' if mode=='Debug' else 's',
                                 'SWIFT_OPTIMIZATION_LEVEL':'-Onone' if mode=='Debug' else '-O',
                                 'DEBUG_INFORMATION_FORMAT':'dwarf' if mode=='Debug' else 'dwarf-with-dsym'})
                if mode=='Debug': settings['SWIFT_ACTIVE_COMPILATION_CONDITIONS']='DEBUG'
            body=' '.join(f'{key} = {quote(value)};' for key,value in settings.items())
            ids.append(obj(scope+mode,f'isa = XCBuildConfiguration; buildSettings = {{ {body} }}; name = {mode};'))
        configs[scope]=obj(scope+'configs',f'isa = XCConfigurationList; buildConfigurations = ({",".join(ids)},); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;')
    target=obj('target',f'isa = PBXNativeTarget; buildConfigurationList = {configs["target"]}; buildPhases = ({sources},{framework_phase},{resource_phase},); buildRules = (); dependencies = (); name = PartScan; productName = PartScan; productReference = {product}; productType = "com.apple.product-type.application";')
    project=obj('project',f'isa = PBXProject; attributes = {{ LastUpgradeCheck = 1600; TargetAttributes = {{ {target} = {{ CreatedOnToolsVersion = 16.0; }}; }}; }}; buildConfigurationList = {configs["project"]}; compatibilityVersion = "Xcode 14.0"; developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en,Base,); mainGroup = {group}; productRefGroup = {products}; projectDirPath = ""; projectRoot = ""; targets = ({target},);')
    project_dir=ROOT/'PartScan.xcodeproj'; project_dir.mkdir(exist_ok=True)
    (project_dir/'project.pbxproj').write_text('// !$*UTF8*$!\n{\n\tarchiveVersion = 1;\n\tclasses = {};\n\tobjectVersion = 56;\n\tobjects = {\n'+'\n'.join(objects)+'\n\t};\n\trootObject = '+project+';\n}\n')
    scheme=f'''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="1600" version="1.3">
  <BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{target}" BuildableName="PartScan.app" BlueprintName="PartScan" ReferencedContainer="container:PartScan.xcodeproj"/></BuildActionEntry></BuildActionEntries></BuildAction>
  <TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES"><Testables/></TestAction>
  <LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" debugServiceExtension="internal" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{target}" BuildableName="PartScan.app" BlueprintName="PartScan" ReferencedContainer="container:PartScan.xcodeproj"/></BuildableProductRunnable></LaunchAction>
  <ProfileAction buildConfiguration="Release" shouldUseLaunchSchemeArgsEnv="YES" savedToolIdentifier="" useCustomWorkingDirectory="NO" debugDocumentVersioning="YES"><BuildableProductRunnable runnableDebuggingMode="0"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{target}" BuildableName="PartScan.app" BlueprintName="PartScan" ReferencedContainer="container:PartScan.xcodeproj"/></BuildableProductRunnable></ProfileAction>
  <AnalyzeAction buildConfiguration="Debug"/><ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/>
</Scheme>
'''
    scheme_path=project_dir/'xcshareddata'/'xcschemes'; scheme_path.mkdir(parents=True,exist_ok=True)
    (scheme_path/'PartScan.xcscheme').write_text(scheme)
    print(f'Xcode project includes {len(source_files)} source files and {len(resources)} resources.')

if __name__=='__main__': main()
