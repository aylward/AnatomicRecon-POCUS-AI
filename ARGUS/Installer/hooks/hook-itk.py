import site
from os import path
from PyInstaller.utils.hooks import get_package_paths

def from_site(filename):
    for sitepath in site.getsitepackages():
        if path.exists(path.join(sitepath, filename)):
            return path.join(sitepath, filename)
    
    alt_paths = ["/c/src/ITK-Release/Wrapping/Generators/Python"]
    for sitepath in alt_paths:
        if path.exists(path.join(sitepath, filename)):
            return path.join(sitepath, filename)
    return None

_, pkgpath = get_package_paths('itk')


datas = [
    (path.join(pkgpath, 'Configuration'), path.join('itk', 'Configuration')),
    (path.join(pkgpath, '*.py'), 'itk'),
    (path.join(pkgpath, '*.pyd'), 'itk'),
]

binaries = []
if from_site('itk_core.libs') != None:
    binaries.append( (path.join(from_site('itk_core.libs'),'*.dll'),
                      'itk_core.libs') )
if from_site('itk_tubetk.libs') != None:
    binaries.append( (path.join(from_site('itk_tubetk.libs'),'*.dll'),
                      'itk_tubetk.libs') )
if from_site('itk_minimalpathextraction.libs') != None:
    binaries.append(
            (path.join(from_site('itk_minimalpathextraction.libs'),'*.dll'),
             'itk_minimalpathextraction.libs') )

#(path.join(from_site('itk_meshtopolydata.libs'), '*.dll'), 'itk_meshtopolydata.libs'),
