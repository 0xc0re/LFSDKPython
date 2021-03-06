import sys
import os
import functools
import clr

#Define global vars
LF = None
IS_IPY = 'GetClrType' in dir(clr)
DEBUG = False

# Hack for running pdb under ipy. Local path not automatically added to sys
if 'pdb' in sys.modules:
    sys.path.insert(0, os.getcwd())
    DEBUG = True

# import CLR and environment paths
clr.AddReference("System")
clr.AddReference("System.IO")
clr.AddReference("System.Reflection")
from System import *
from System.IO import FileNotFoundException
from System.Reflection import *
from environment import Environment

def GetModuleAttr(module, attr):
    try:
        return getattr(module, attr)
    except AttributeError:
        return None

class LFModuleInstanceWrapper:
    #accepts an instance of an object
    #sets a property capturing the properties of that object instance
    def __init__(self, instance):
        self._instance = instance
        self._calling_method = None
        
        #this is to handle the possibility of void being passed to the constructor
        try:
            self._objProps = self._instance.GetType().GetProperties()
        except:
            pass

    #overload to output the object instance and not the wrapper
    def __repr__ (self):
        return self._instance.__repr__()
    
    #overload the attribute getter
    #check the internal object properties first, and return a wrapped instance of the result if it is found
    #otherwise, assume we are calling one of the object's methods, so invoke a helper function to handle that
    def __getattr__ (self, attr):
        #TODO add type check to prevent boxing of POCOs
        for x in range(self._objProps.Length):
            if self._objProps[x].Name == attr:
                return LFModuleInstanceWrapper(self._objProps[x].GetValue(self._instance))
        #if this is one of python's magic methods unbox and pass to the instance
        if attr.startswith('__'):
            return getattr(self.Unbox(), attr)
            #return self.Unbox if IS_IPY else getattr(self.Unbox(), attr)
        else:
            self._calling_method = attr
            return self._Call
    
    #overload the attribute setter such it properly handles assigning to .NET properties vs. Python properties
    def __setattr__(self, name, value):
        if "_" in name:
            self.__dict__[name] = value
        else:
            for x in range(self._objProps.Length):
                if self._objProps[x].Name == name:
                    if hasattr(value, '_instance'):
                        self._objProps[x].SetValue(self._instance, value._instance)
                    else:
                        self._objProps[x].SetValue(self._instance, value)
    
    #this method facilitates the conversion of basic .NET objects back to Python objects
    def Unbox (self):
        return self._instance
    
    #method to call the appropriate overload of the internal object's methods given the provided arguments
    def _Call (self, *argv):
        def _checkTypes(method, types):
            p_enums = [i for i,t in enumerate(types) if t.Name == u'Int32']
            p_types = [p.ParameterType for p in method.GetParameters()]
            type_pairs = zip(p_types, types)
            type_checks = map(lambda (i,e): e[0] == e[1] or i in p_enums and e[0].IsEnum, enumerate(type_pairs))
            return not False in type_checks

        def _handleEnum(inst_type, method_name, arg_types):
            #get all methods that match the method name and have the correct parameter count
            methods = [m for m in inst_type.GetMethods() if m.Name == method_name and m.GetParameters().Length == arg_sig['types'].Length]
            targets = filter(lambda m: _checkTypes(m, arg_types), methods) 
            return targets[0] if len(targets) > 0 else None

        if self._calling_method is None:
            raise KeyError("No method has been specified to be called!")
        elif self._calling_method == 'Unbox':
            return self._instance
        
        #check arguments and throw exception is there are None references
        #the wrapper uses the calling arguments types to infer which overload to invoke
        if None in argv:
            raise KeyError("Arguments of type 'NoneType' not supported within the wrapper.")
        
        try:
            method_name = self._calling_method
            inst_type = self._instance.GetType()
            arg_sig = self._GetArgSignature(argv)

            target_method = inst_type.GetMethod(method_name, arg_sig['types'] if len(arg_sig['types']) > 0 else Type.EmptyTypes)
            if target_method is None:
                target_method = _handleEnum(inst_type, method_name, arg_sig['types'])
                if target_method is None:
                    raise KeyError("No overload of the provided method exists given the provided argument types!")
                else:
                    return LFModuleInstanceWrapper(target_method.Invoke(self._instance, arg_sig['values']))
            else:
                #return the retrieved method
                return LFModuleInstanceWrapper(target_method.Invoke(self._instance, arg_sig['values']))
        except Exception as e:
            print e.InnerException
            raise e
                
    def _GetArgSignature(self, args):
        arg_types = []
        arg_vals = []
        for arg in args:
            if hasattr(arg, '_instance'):
                arg_types.append(arg._instance.GetType())
                arg_vals.append(arg._instance)
            else:
                arg_types.append(type(arg))
                arg_vals.append(arg)
        return {'types': Array[Type](arg_types), 'values': Array[Object](arg_vals)}

class LFModuleWrapper:
    #method to invoke the proper constructor of the given class given the arguments
    #returns LFModuleInstanceWrapper object that is constructed with output object instance of the called constructor
    def _construct (self, argv):
        if self._module is None:
            raise KeyError("No class has been provided!")
        try:
            arg_sig = self._GetArgSignature(argv)
            mod_type = self._GetClrType()

            target_constructor = mod_type.GetConstructor(arg_sig['types'] if len(arg_sig['types']) > 0 else Type.EmptyTypes)
            if target_constructor is None:
                raise KeyError("No overload of the provided class constructor exists given the provided argument types!")
            else:
                #return the retrieved method as an instance of LFModuleInstanceWrapper
                 return LFModuleInstanceWrapper(target_constructor.Invoke(arg_sig['values']))
        except Exception as e:
            print e.InnerException
            raise e
        return self
            
    #overload to output the module and not the wrapper
    def __repr__ (self):
        return self._module.__repr__()
    
    #overload the __get__ to handle static properties and methods
    def __getattr__ (self, attr):
        self._calling_method = attr
        #check if the property is an ENUM, Enums return back ints 
        enum_val = GetModuleAttr(self._module, attr)

        if enum_val == None:
            raise KeyError("{} is not a valid value for {}".format(attr, self._module))
        elif type(enum_val) == int:
            return enum_val 
        else:
            return self._Call
        
    #overload the __call__ to invoke our wrapper constructor (unless no args are provided, in which case just output the module)
    def __call__(self, *argv):
        if argv is None:
            return self._module
        return self._construct(argv)
    
    #method to call the appropriate overload of the static's methods given the provided arguments
    def _Call (self, *argv):
        def _checkTypes(method, types):
            p_enums = [i for i,t in enumerate(types) if t.Name == u'Int32']
            p_types = [p.ParameterType for p in method.GetParameters()]
            type_pairs = zip(p_types, types)
            type_checks = map(lambda (i,e): e[0] == e[1] or i in p_enums and e[0].IsEnum, enumerate(type_pairs))
            return not False in type_checks

        def _handleEnum(mod_type, method_name, arg_types):
            #get all methods that match the method name and have the correct parameter count
            methods = [m for m in mod_type.GetMethods() if m.Name == method_name and m.GetParameters().Length == arg_sig['types'].Length]
            targets = filter(lambda m: _checkTypes(m, arg_types), methods) 
            return targets[0] if len(targets) > 0 else None

        if self._calling_method is None:
            raise KeyError("No method has been specified to be called!")
        #check arguments and throw exception is there are None references
        #the wrapper uses the calling arguments types to infer which overload to invoke
        if None in argv:
            raise KeyError("Arguments of type 'NoneType' not supported within the wrapper.")

        try:
            method_name = self._calling_method
            arg_sig = self._GetArgSignature(argv)
            mod_type = self._GetClrType()

            #try and find the appropriate orverloaded method based on the argument type signature
            target_method = mod_type.GetMethod(method_name, arg_sig['types'] if len(arg_sig['types']) > 0 else Type.EmptyTypes)
            if target_method is None:
                target_method = _handleEnum(mod_type, method_name, arg_sig['types'])
                if target_method is None:
                    raise KeyError("No overload of the provided method exists given the provided argument types!")
                else :
                    return LFModuleInstanceWrapper(target_method.Invoke(self._module, arg_sig['values']))
            else:
                #return the retrieved method
                return LFModuleInstanceWrapper(target_method.Invoke(self._module, arg_sig['values']))
        except Exception as e:
            print e.InnerException
            raise e

    def _GetClrType(self, mod=None, ver=None):
        #if args are not passed pull from the instance
        mod = self._module if mod == None else mod
        ver = self._version if ver == None else ver

        #if running in ipy use the built in lib
        if IS_IPY:
            return clr.GetClrType(mod)
        else:
            class_name = mod.__module__ + '.' + mod.__name__
            namespace = mod.__module__
            #hack to handle ClientAutomation library
            if namespace.lower() == 'laserfiche.clientautomation':
                namespace = 'ClientAutomation'

            qual_name = r'{}, {}, Version={}.0.0, Culture=neutral'.format(
                class_name, namespace, ver
            )
            return Type.GetType(qual_name)

    #accepts a namespace or class
    def __init__(self, module, ver):
        self._module = module
        self._version = ver

    def _GetArgSignature(self, args):
        arg_types = []
        arg_vals = []
        for arg in args:
            if hasattr(arg, '_instance'):
                arg_types.append(arg._instance.GetType())
                arg_vals.append(arg._instance)
            else:
                arg_types.append(type(arg))
                arg_vals.append(arg)
        #box arrays into .NET types for IronPython support
        return {'types': Array[Type](arg_types), 'values': Array[Object](arg_vals)}

# Define an instance of the LF ClR. Valid Args are:
# target = <SDK Target>.  Valid options are:
#       
class LFWrapper:
    def __init__(self, argv = None):
        '''
        args:
           RepositoryAccess - An object which maps version to a dll on the local disk
           LFSOPaths: An object that maps version numbers to a dll on the local disk
        '''
        def initialize_module_store(paths, val = None):
            output = { }
            for key in paths:
                output[key] = val 
            return output

        self._args = argv if argv else Environment()
        self._lf_credentials = self._args.LaserficheConnection
        self._loaded_modules = { 
            'LFSO': initialize_module_store(self._args.LFSO_Paths, { }),
            'DocumentProcessor': initialize_module_store(self._args.DocumentProcessor_Paths, { }),
            'RepositoryAccess': initialize_module_store(self._args.RepositoryAccess_Paths, { })
        }
        self._sdk = None
        self._lf_session = None
        self._db = None

    def __repr__(self):
        return 'LF SDK Wrapper'

    # try to pull a target attribute from RA. Search order is DocumentService, ClientAutomation, RepositoryAccess, SecurityTokenService
    def _get_fromRA(self, module, attr, ver):
        ns_search_list = ['DocumentService', 'ClientAutomation', 'RepositoryAccess', 'SecurityToken']
        namespaces = [ns for ns in map(lambda n: GetModuleAttr(module, n), ns_search_list) if ns != None]
        for ns in namespaces:
            target = GetModuleAttr(ns, attr)
            if target != None:
                return LFModuleWrapper(target, ver)
            else:
                continue
        #if no match is found raise an exception
        raise KeyError('Command not found')

    # LFSOd does not have namespacing infront of the methods.  We can check the module dir directly and return
    def _get_fromCOM(self, module, attr):
        for mod in module.keys():
            namespaces = dir(module[mod])
            if attr in namespaces:
                return LFModuleWrapper(getattr(module[mod], attr))
        raise KeyError('Command not found')

    # this is used to overload the property operator for the LFWrapper object
    # it will allow short cut access to SDK objects through the wrapper without having to go through
    # the namespaces or import specific functions from the module.
    def __getattr__(self, attr):
        if self._sdk == None:
            raise Exception('SDK is not loaded')
        else:
            type = self._sdk['type']
            module = self._sdk['module']
            version = self._sdk['version']
            return self._get_fromRA(module, attr, version) if type == 'RA' else self._get_fromCOM(module, attr)
    
    def Connect(self, **kwargs):
        #helper functions to connect to either LFSO or RA
        def ConnectRA(server, database, username, password):
            if self._lf_session == None:
                if username:
                    credentials = (server, database, username, password)
                else:
                    credentials = (server, database)
            else:
                raise Exception('Please load a version of the SDK')
            
            self._lf_session = self.Session.Create(*credentials)
            return self._lf_session

        def ConnectLfso(server, database, username, password):
            if self._db == None:
                credentials = (database, server, username, password)
                app = self.LFApplicationClass()
                self._db = app.ConnectToDatabase(*credentials)
            return self._db
        
        def GetDefaultCred(key, arg_list):
            try:
                return arg_list[key]
            except KeyError:
                return self._lf_credentials[key]
            
        #Function Logic Starts here
        #if args are not given pull from environment.py
        server = GetDefaultCred('server', kwargs)
        database = GetDefaultCred('database', kwargs)
        username = GetDefaultCred('username', kwargs) if not 'server' in kwargs or 'username' in kwargs else ''
        password = GetDefaultCred('password', kwargs) if not 'server' in kwargs or 'password' in kwargs else ''
        creds = (server, database, username, password)

        sdk_loaded = self._sdk != None
        if sdk_loaded:
            type = self._sdk['type']
            return ConnectRA(*creds) if type == 'RA' else ConnectLfso(*creds)
        else:
            raise Exception('Please load a version of the SDK')

    def Disconnect(self):
        def DisconnectLfso():
            try:
                self._db.CurrentConnection.Terminate()
            except:
                print 'Could not close LFSO connection. Please ensure that you have opened one!'

        def DisconnectRA():
            try:
                self._lf_session.Close()
            except Exception as e:
                print e
                print 'Could not close RA session. Please ensure that you have opened one!'

        sdk_loaded = self._sdk != None
        if sdk_loaded:
            type = self._sdk['type']
            return DisconnectRA() if type == 'RA' else DisconnectLfso()
        else:
            raise Exception('Please load a version of the SDK')

    def GetSession(self):
        if self._lf_session.value != None:
            return self._lf_session
        else:
            raise Exception('Not logged in!')

    def GetCredentials(self):
        if self._lf_credentials:
            return self._lf_credentials

    def LoadCom(self, version, module_name):
        if module_name == "LFSO":
            self.LoadLfso(version)
        elif module_name == "DocumentProcessor":
            self.LoadDocumentProcessor(version)
        else:
            raise Exception('Unsupported COM SDK module')
    
    def LoadLfso(self, version):
        lfso_modules = self._loaded_modules['LFSO']
        module = None
        lib_name = None
        
        if version in lfso_modules.keys() and lfso_modules[version] != {} : 
            module = lfso_modules[version]
        else:
            try:
                #get the dll path and library name
                dll_path = self._args.LFSO_Paths[version]
                lib_name = 'LFSO{}Lib'.format(version.translate(None, '.'))

                #loads the LFSO reference and add it to the loaded modules list
                #tries to load from GAC first
                try:
                    clr.AddReference("Interop." + lib_name)
                    module = __import__(lib_name)
                    lfso_modules[version] = module
                except:
                    clr.AddReferenceToFileAndPath(dll_path)
                    module = __import__(lib_name) 
                    lfso_modules[version] = module
            except Exception:
                print 'Laserfiche Server Object v{} could not be found. Please check your environment.py file'.format(version)
                
        #if a module was found set it as the new default
        if module != None:
            if self._sdk is None:
                self._sdk = {'type': 'LFSO', 'module': { 'LFSO': module }} 
            else:
               	self._sdk['module']['LFSO'] = module
        return module
    
    def LoadDocumentProcessor(self, version):
        doc_modules = self._loaded_modules['DocumentProcessor']
        module = None
        
        if version in doc_modules.keys() and doc_modules[version] != {} : 
            module = doc_modules[version]
        else:
            try:
                #get the dll path and library name
                dll_path = self._args.DocumentProcessor_Paths[version]
                lib_name = 'DocumentProcessor'+version.translate(None, '.')

                #loads the DocumentProcessor reference and add it to the loaded modules list
                #tries to load from GAC first
                try:
                    clr.AddReference("Interop." + lib_name)
                    module = __import__(lib_name)
                    doc_modules[version] = module
                except:
                    clr.AddReferenceToFileAndPath(dll_path)
                    module = __import__(lib_name) 
                    doc_modules[version] = module
            except Exception:
                print 'DocumentProcessor v{} could not be found. Please check your environment.py file'.format(version)

        #if a module was found set it as the new default
        if module != None:
            if self._sdk is None:
                self._sdk = { type: 'LFSO', 'module': {'DocumentProcessor':  module } }
            else:
                self._sdk['module']['DocumentProcessor'] = module
        
        return module
    
    def LoadRA(self, version, module_name):
        def load_from_GAC(module_name, version):
            ra_pk_token = '3f98b3eaee6c16a6'
            ca_pk_token = '607dd73ee2bd1c00'

            namespace = 'Laserfiche.{}'.format(module_name)
            version = r'{}.0.0'.format(version)
            module_name = module_name if module_name.lower() == 'clientautomation' else r'Laserfiche.{}'.format(module_name)
            token = ca_pk_token if module_name.lower() == 'clientautomation' else ra_pk_token
            assembly_name = (r'{}, Version={}, Culture=neutral, PublicKeyToken={}'
                             ).format(module_name, version, token)
            try:
                clr.AddReference(assembly_name)
                return __import__(namespace)
            except FileNotFoundException:
                return None 

        def load_from_file(module_name, version):
            namespace = 'Laserfiche.{}'.format(module_name)
            dll_path = (r'{}\{}.dll' if module_name == 'ClientAutomation' else r'{}\Laserfiche.{}.dll'
                        ).format(self._args.RepositoryAccess_Paths[version], module_name)
            #IronPython uses a differnt method to load from file
            try:
                if 'AddReferenceToFileAndPath' in dir(clr):
                    clr.AddRefernceToFileAndPath(dll_path)
                else:
                    clr.AddReference(dll_path)
                return __import__(namespace)
            except FileNotFoundException:
                return None

        ra_modules = self._loaded_modules['RepositoryAccess']
        module_whitelist = ['RepositoryAccess', 'DocumentServices', 'ClientAutomation'] 
        module = None

        #Check to see if the module has already been loaded and is in the cache
        if version in ra_modules.keys() and module_name in ra_modules[version].keys():
            module = ra_modules[version][module_name]
        else:
            try:
                #try to load the library from the GAC
                module = load_from_GAC(module_name, version)
                #if not found in the gac try to load by file path
                if module == None:
                    module = load_from_file(module_name, version)
                #if not found, raise exception and break out
                if module == None:
                    raise FileNotFoundException(r'{} v{} could not be found. Please ensure the library is in the gac or your environment.py file'.format(module_name, version))
                #Add module to the cache
                self._loaded_modules['RepositoryAccess'][version] = {'type': 'RA', 'module': module, 'version': version}
            except FileNotFoundException as ex:
                print ex.Message
        if module != None:
            self._sdk = self._loaded_modules['RepositoryAccess'][version] 

        return module

def main() :
    global LF
    LF = LFWrapper(Environment())

# run for debugging
def debug():
    global LF
    LF = LFWrapper(Environment())
    LF.LoadRA('10.2', 'RepositoryAccess')
    LF.Connect(server = 'localhost', database = 'Dev301', username='admin', password='a')

    sess = LF.GetSession()
    root = LF.Folder.GetRootFolder(sess)
    LF.Folder.Create(root, "test", LF.EntryNameOption.AutoRename, sess)

# Run main if not loaded as a module
if __name__ == '__main__':
    if DEBUG:
        debug()
    else:
        main()

