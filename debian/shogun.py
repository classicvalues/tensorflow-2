#!/usr/bin/python3.6
# TF-Shogun: Distribution Friendly Light-Weight Build for TensorFlow.
#
#Copyright: 2018 Mo Zhou <lumin@debian.org>
#License: Expat
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
# .
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
# .
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, 
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE 
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

'''
TF-Shogun
=========

Distribution friendly light-weight build for TensorFlow.
Especially written for Debian GNU/Linux.

Shogun needs the bazel dumps from bazelQuery.sh .
And it tries to produce similar result to that from bazel.

References
----------

1. CMake build, tensorflow/contrib/cmake
2. Makefile build, tensorflow/contrib/makefile
3. TensorFlow's native Bazel build.
4. For extra compiler definitions .e.g TENSORFLOW_USE_JEMALLOC please lookup
   tensorflow/core/platform/default/build_config.bzl
5. ArchLinux PKGBUILD
   https://git.archlinux.org/svntogit/community.git/tree/trunk/PKGBUILD?h=packages/tensorflow
6. Gentoo ebuild
   https://packages.gentoo.org/packages/sci-libs/tensorflow
'''
# FIXME: libs are still not linked against mkl-dnn, xsmm, openblas etc.
# FIXME: how to use blas/mkl to improve speed?
# FIXME: they all should depend on libtensorflow_framework?
# FIXME: automatically generate installer from shogun

from typing import *
import sys
import re
import os
import argparse
import json
import glob
import subprocess
from pprint import pprint
from ninja_syntax import Writer

import numpy
import distutils.sysconfig


# FIXME: don't forget to bump soversion when upstream version changes!
tf_soversion = '2.0'
py_incdir = distutils.sysconfig.get_python_inc()
py_libdir = distutils.sysconfig.get_python_lib()
py_ver    = distutils.sysconfig.get_python_version()
py_numpy_incdir = numpy.get_include()


def ninjaCommonHeader(cursor: Writer, ag: Any) -> None:
    '''
    Writes a common header to the ninja file. ag is parsed arguments.
    '''
    cursor.comment('-- start common ninja header --')
    cursor.comment(f'Note, this ninja file was automatically generated by {__file__}')
    cursor.newline()
    cursor.comment('-- compiling tools --')
    cursor.newline()
    cursor.variable('CXX', 'g++')
    cursor.variable('PROTOC', '/usr/bin/protoc')
    cursor.variable('PROTO_TEXT', f'./proto_text')
    cursor.variable('SHOGUN_EXTRA', '') # used for adding specific flags for a specific target
    cursor.newline()
    cursor.comment('-- compiler flags --')
    cursor.newline()
    cursor.variable('CPPFLAGS', '-D_FORTIFY_SOURCE=2 ' + str(os.getenv('CPPFLAGS', '')))
    cursor.variable('CXXFLAGS', '-std=c++14 -O2 -pipe -fPIC -gsplit-dwarf -DNDEBUG'
        + ' -fstack-protector-strong -w ' + str(os.getenv('CXXFLAGS', '')))
    cursor.variable('LDFLAGS', '-Wl,-z,relro -Wl,-z,now ' + str(os.getenv('LDFLAGS', '')))
    cursor.variable('INCLUDES', '-I. -I./debian/embedded/eigen3 -I./third_party/eigen3/'
            + ' -I/usr/include/gemmlowp -I/usr/include/llvm-c-7'
            + ' -I/usr/include/llvm-7 -Ithird_party/toolchains/gpus/cuda/'
            + ' -I./debian/embedded/abseil/')
    cursor.variable('LIBS', '-lpthread -lprotobuf -lnsync -lnsync_cpp -ldouble-conversion'
	+ ' -ldl -lm -lz -lre2 -ljpeg -lpng -lsqlite3 -llmdb -lsnappy -lgif -lLLVM-7')
    cursor.newline()
    cursor.comment('-- compiling rules-- ')
    cursor.rule('rule_PROTOC', f'$PROTOC $in --cpp_out . $SHOGUN_EXTRA')
    cursor.rule('rule_PROTOC_GRPC', f'$PROTOC --grpc_out . --cpp_out . --plugin protoc-gen-grpc=/usr/bin/grpc_cpp_plugin $in')
    cursor.rule('rule_PROTOC_PYTHON', '$PROTOC --python_out . -I. $in')
    cursor.rule('rule_PROTO_TEXT', f'$PROTO_TEXT tensorflow/core tensorflow/core tensorflow/tools/proto_text/placeholder.txt $in')
    cursor.rule('rule_CXX_OBJ', f'$CXX $CPPFLAGS $CXXFLAGS $INCLUDES $SHOGUN_EXTRA -c $in -o $out')
    cursor.rule('rule_CXX_EXEC', f'$CXX $CPPFLAGS $CXXFLAGS $INCLUDES $LDFLAGS $LIBS $SHOGUN_EXTRA $in -o $out')
    cursor.rule('rule_CXX_SHLIB', f'$CXX -shared -fPIC $CPPFLAGS $CXXFLAGS $INCLUDES $LDFLAGS $LIBS $SHOGUN_EXTRA $in -o $out')
    cursor.rule('rule_CC_OP_GEN', f'LD_LIBRARY_PATH=. ./$in $out $cc_op_gen_internal tensorflow/core/api_def/base_api')
    cursor.rule('rule_PY_OP_GEN', f'LD_LIBRARY_PATH=. ./$in tensorflow/core/api_def/base_api,tensorflow/core/api_def/python_api 1 > $out')
    cursor.rule('COPY', f'cp $in $out')
    cursor.rule('rule_ANYi', f'$ANY $in')
    cursor.rule('rule_ANYo', f'$ANY $out')
    cursor.rule('rule_ANY', '$ANY')
    cursor.rule('rule_ANYio', '$ANY $in $out')
    cursor.newline()
    cursor.comment('-- end common ninja header --')
    cursor.newline()


def cyan(s: str) -> str:
    return f'\033[1;36m{s}\033[0;m'

def yellow(s: str) -> str:
    return f'\033[1;33m{s}\033[0;m'

def red(s: str) -> str:
    return f'\033[1;31m{s}\033[0;m'


def eGrep(pat: Any, lst: List[str]) -> (List[str], List[str]):
    '''
    Just like grep -E
    pat could be str or List[str]
    '''
    match, unmatch = [], []
    if not any((isinstance(pat, str), isinstance(pat, list))):
        raise TypeError("Undefined argument type")
    pat = pat if isinstance(pat, list) else [pat]
    for item in lst:
        if any(re.match(x, item) for x in pat):
            match.append(item)
        else:
            unmatch.append(item)
    return match, unmatch


def eComplain(lst: List[str]) -> None:
    '''
    Print warning message if detected unprocessed files.
    '''
    if not lst: return
    for x in lst:
        print(yellow('? HowToDealWith'), x)
    print(red(f'{len(lst)} files to be dealt with left unresolved'))


def eGlob(pat: str, *, filt: List[str] = [], vfilt: List[str] = []) -> List[str]:
    '''
    Extended version of glob.glob, which globs file and apply
    filters and reverse filters on the result.
    '''
    globs = glob.glob(pat, recursive=True)
    for f in filt:
        globs, _ = eGrep(f, globs)
    for vf in vfilt:
        _, globs = eGrep(f, globs)
    return globs


def eUniq(pat: str, rep: str, lst: List[str]) -> List[str]:
    '''
    lst -> re.sub(pat, rep, ...) -> uniq -> return
    '''
    return list(sorted(set([re.sub(pat, rep, x) for x in lst])))


def getDpkgArchitecture(query: str) -> str:
    '''
    dpkg-architecture -qQUERY
    '''
    # XXX: I wish we don't need to use this function.
    result = subprocess.Popen(['dpkg-architecture', f'-q{query}'],
             stdout=subprocess.PIPE).communicate()[0].decode().strip()
    return result


def systemShell(command: List[str]) -> str:
    '''
    Execute the given command in system shell. Unlike os.system(), the program
    output to stdout and stderr will be returned.
    '''
    result = subprocess.Popen(command, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE).communicate()[0].decode().strip()
    return result


def bazelPreprocess(srclist: List[str]) -> List[str]:
    '''
    1. Filter out external dependencies from bazel dependency dump.
    2. Mangle file path.
    3. Report the depending libraries.
    '''
    deplist, retlist = set([]), []
    for src in srclist:
        if re.match('^@(\w*).*', src):
            # It's an external dependency
            deplist.update(re.match('^@(\w*).*', src).groups())
        elif re.match('^..third_party.*', src):
            pass # ignore
        else:
            # it's an tensorflow source
            retlist.append(re.sub('^//', '', re.sub(':', '/', src)))
    print(cyan('Required Depends:'))
    pprint(deplist, indent=4, compact=True)
    print('Globbed', cyan(f'{len(srclist)}'), 'source files')
    return retlist


###############################################################################
###############################################################################


def shogunProtoText(argv):
    '''
    Build a binary ELF executable named proto_text, which generates
    XXX.pb_text{.cc,.h,-impl.h} files from a given XXX.proto file.
    This binary file is for one-time use.

    Input: bazelDump
    Output: proto_text
    '''
    ag = argparse.ArgumentParser()
    ag.add_argument('-i', type=str, required=True,
            help='list of source files')
    ag.add_argument('-g', type=str, required=True,
            help='list of generated files')
    ag.add_argument('-o', type=str, default='proto_text.ninja',
            help='where to write the ninja file')
    ag = ag.parse_args(argv)
    print(red('Argument Dump:'))
    pprint(vars(ag))

    # (0) read bazel dump and apply hardcoded filters
    srclist = bazelPreprocess([l.strip() for l in open(ag.i, 'r').readlines()])
    genlist = bazelPreprocess([l.strip() for l in open(ag.g, 'r').readlines()])
    srclist.extend(genlist)
    _, srclist = eGrep('.*.h$', srclist) # nothing to do
    _, srclist = eGrep('^third_party', srclist) # no third_party stuff
    _, srclist = eGrep('.*windows/.*', srclist) # no windoge source
    _, srclist = eGrep('.*.proto$', srclist) # nothing to do 

    # (1) Instantiate ninja writer and generate targets
    cursor = Writer(open(ag.o, 'w'))
    ninjaCommonHeader(cursor, ag)
    cclist, srclist = eGrep('.*.cc$', srclist)
    objlist = []
    for cc in cclist:
        obj = cc.replace('.cc', '.o')
        cursor.build(obj, 'rule_CXX_OBJ', cc)
        objlist.append(obj)

    # (2) link objects into ELF: proto_text
    cursor.build(f'proto_text', 'rule_CXX_EXEC', objlist,
            variables={'LIBS': '-lpthread -lprotobuf -ldouble-conversion'})

    # (.) finish
    cursor.close()
    eComplain(srclist)


def shogunTFLib_framework(argv):
    '''
    Build libtensorflow_framework.so, and a byproduct for tf_cc_op_gen.
    '''
    ag = argparse.ArgumentParser()
    ag.add_argument('-i', type=str, required=True,
            help='list of source files')
    ag.add_argument('-g', type=str, required=True,
            help='list of generated files')
    ag.add_argument('-o', type=str, default='libtensorflow_framework.ninja',
            help='where to write the ninja file', )
    ag.add_argument('-H', type=str, default='libtensorflow_framework.hdrs',
            help='a list of header files')
    ag.add_argument('-O', type=str, required=True,
            help='file name of shared object')
    ag.add_argument('-b', type=str, required=True,
            help='file name of the byproduce')
    ag = ag.parse_args(argv)
    print(red('Argument Dump:'))
    pprint(vars(ag))

    # (0) read bazel dump and apply hardcoded filters
    srclist = bazelPreprocess([l.strip() for l in open(ag.i, 'r').readlines()])
    genlist = bazelPreprocess([l.strip() for l in open(ag.g, 'r').readlines()])
    srclist.extend(genlist)
    _, srclist = eGrep('.*proto_text.gen_proto_text_functions.cc', srclist)
    _, srclist = eGrep('^third_party', srclist)
    _, srclist = eGrep('.*/windows/.*', srclist) # no windoge source.
    _, srclist = eGrep('.*.proto$', srclist) # nothing to do
    Rheaders, srclist = eGrep('.*.h$', srclist)  # nothing to do

    # (1) Initialize ninja file and generate object targets
    cursor = Writer(open(ag.o, 'w'))
    ninjaCommonHeader(cursor, ag)
    cclist, srclist = eGrep('.*.cc', srclist)
    objlist = []
    for cc in cclist:
        obj = cc.replace('.cc', '.o')
        cursor.build(obj, 'rule_CXX_OBJ', cc)
        objlist.append(obj)

    # (2) link the shared object
    libs = '''farmhash highwayhash snappy gif double-conversion
              z protobuf jpeg nsync nsync_cpp pthread
              '''.split()
    libs = ' '.join(f'-l{x}' for x in libs)
    extra = f'''-Wl,--soname={ag.O}.{tf_soversion}
                -fvisibility=hidden
                -Wl,--version-script tensorflow/tf_framework_version_script.lds
             '''.split()
    extra = ' '.join(x for x in extra)
    cursor.build(ag.O, 'rule_CXX_SHLIB', inputs=objlist,
        variables={'LIBS': libs, 'SHOGUN_EXTRA': extra})

    # (3) link the byproduct for tf_cc_op_gen
    _, libtfccopgen = eGrep(['.*core/kernels.*', '.*core/ops.*'], objlist)
    cursor.build(ag.b, 'rule_CXX_SHLIB', libtfccopgen,
            variables={'LIBS': libs})

    # done
    cursor.close()
    eComplain(srclist)


def shogunTFLib(argv):
    '''
    Build any one of the following libraries:
    * libtensorflow.so
    * libtensorflow_cc.so
    * _pywrap_tensorflow_internal.so
    '''
    ag = argparse.ArgumentParser()
    ag.add_argument('-i', type=str, required=True,
            help='list of source files')
    ag.add_argument('-g', type=str, required=True,
            help='list of generated files')
    ag.add_argument('-o', type=str, required=True,
            help='where to write the ninja file')
    ag.add_argument('-O', type=str, required=True,
            help='the file name shared object')
    ag.add_argument('-H', type=str, required=True,
            help='where to put the headers list')
    ag = ag.parse_args(argv)
    print(red('Argument Dump:'))
    pprint(vars(ag))

    # (0) read bazel dump and apply hard-coded filters
    srclist = bazelPreprocess([l.strip() for l in open(ag.i, 'r').readlines()])
    genlist = bazelPreprocess([l.strip() for l in open(ag.g, 'r').readlines()])
    extra_srcs = ['debian/embedded/fft/fftsg.c']
    srclist = list(set(srclist + genlist + extra_srcs))
    _, srclist = eGrep('^third_party', srclist)
    _, srclist = eGrep('.*/windows/.*', srclist) # no windoge source.
    _, srclist = eGrep('.*.cu.cc$', srclist) # no CUDA file for CPU-only build
    _, srclist = eGrep('.*.pbtxt$', srclist) # not for us
    _, srclist = eGrep('.*platform/cloud.*', srclist) # SSL 1.1.1 broke it.
    _, srclist = eGrep('.*platform/s3.*', srclist) # we don't have https://github.com/aws/aws-sdk-cpp
    _, srclist = eGrep('.*_main.cc$', srclist) # don't include any main function.
    _, srclist = eGrep('.*_test.cc$', srclist) # don't include any test
    _, srclist = eGrep('.*gen_proto_text_functions.cc', srclist) # not for this library
    _, srclist = eGrep('.*tensorflow.contrib.cloud.*', srclist) # it wants GoogleAuthProvider etc.
    _, srclist = eGrep('.*gcs_config_ops.cc', srclist) # it wants GcsFileSystem
    Rheaders, srclist = eGrep('.*.h$', srclist) # nothing to do
    _, srclist = eGrep('.*.proto$', srclist) # nothing to do
    _, srclist = eGrep('.*.i$', srclist) # SWIG files aren't for us
    _, srclist = eGrep('.*contrib/gdr.*', srclist) # NO GDR

    if getDpkgArchitecture('DEB_HOST_ARCH') != 'amd64':
        # they FTBFS on non-amd64 arches
        _, srclist = eGrep('.*/core/debug/.*', srclist)
        _, srclist = eGrep('.*debug_ops.*', srclist)

    # (1) Instantiate ninja writer and compile objects
    exception_eigen_avoid_std_array = [
        'sparse_tensor_dense_matmul_op',
        'conv_grad_ops_3d',
        'adjust_contrast_op'
        ]
    if 'pywrap' in ag.O:
        srclist.append('tensorflow/python/framework/fast_tensor_util.cc')
    cursor = Writer(open(ag.o, 'w'))
    ninjaCommonHeader(cursor, ag)
    cclist, srclist = eGrep(['.*.cc$', '.*.c$'], srclist)
    objlist = []
    for cc in cclist:
        obj = re.sub('.c[c]?$', '.o', cc)
        variables = {}
        if any(x in cc for x in exception_eigen_avoid_std_array):
            variables = {'SHOGUN_EXTRA': '-DEIGEN_AVOID_STL_ARRAY'}
        elif 'python' in cc:
            variables = {'SHOGUN_EXTRA': f'-I{py_incdir} -L{py_libdir}'}
        cursor.build(obj, 'rule_CXX_OBJ', cc, variables=variables)
        objlist.append(obj)

    # (2) link the final shared object
    libs = '''pthread protobuf nsync nsync_cpp double-conversion jpeg png
              gif highwayhash farmhash jsoncpp sqlite3 re2 curl lmdb snappy
              dl z m LLVM-7 grpc++
              '''.split()
    libs = ' '.join(f'-l{x}' for x in libs)
    extra = f'''-Wl,--soname={ag.O}.{tf_soversion} -fvisibility=hidden
             '''.split()
    if 'libtensorflow.so' in ag.O:
        extra.append('-Wl,--version-script tensorflow/c/version_script.lds')
    elif 'libtensorflow_cc.so' in ag.O:
        extra.append('-Wl,--version-script tensorflow/tf_version_script.lds')
    elif 'pywrap' in ag.O:
        print(f'_pywrap_tensorflow_internal will be built with python{py_ver}')
        extra.append(f'-I{py_incdir} -L{py_libdir}')
    extra = ' '.join(x for x in extra)
    cursor.build(ag.O, 'rule_CXX_SHLIB', objlist,
            variables={'LIBS': libs, 'SHOGUN_EXTRA': extra})

    # (3) write down the related header files
    with open(ag.H, 'w') as f:
        f.writelines([x + '\n' for x in eUniq('', '', Rheaders)])

    # (.) finish
    cursor.close()
    eComplain(srclist)


def shogunGenerator(argv):
    '''
    Generic File Generator. It determines how to generate the given list
    of files by checking every file name extention.
    '''
    ag = argparse.ArgumentParser()
    ag.add_argument('-g', type=str, required=True,
            help='list of generated files',)
    ag.add_argument('-o', type=str, required=True,
            help='where to write the ninja file')
    ag = ag.parse_args(argv)
    print(red('Argument Dump:'))
    pprint(vars(ag))

    # (0.1) Read the list and apply hard-coded filters.
    Rall = bazelPreprocess([l.strip() for l in open(ag.g, 'r').readlines()])

    # (0.1) Instantiate ninja writer
    cursor = Writer(open(ag.o, 'w'))
    ninjaCommonHeader(cursor, ag)

    # (1.1) Collect protobuf-grpc stuff and generate targets
    Rgrpc_pb_all, Rall = eGrep(['.*.grpc.pb.cc', '.*.grpc.pb.h'], Rall)
    Lgrpc_proto = eUniq('.grpc.pb.cc$', '.proto', Rgrpc_pb_all)
    Lgrpc_proto = eUniq('.grpc.pb.h$', '.proto', Lgrpc_proto)
    for proto in Lgrpc_proto:
        Tcc = re.sub('.proto$', '.grpc.pb.cc', proto)
        Th  = re.sub('.proto$', '.grpc.pb.h', proto)
        if getDpkgArchitecture('DEB_HOST_ARCH') == 'amd64':
            cursor.build([Tcc, Th], 'rule_PROTOC_GRPC', proto)

    # (1.2) Collect protobuf stuff and generate targets
    Rpb_all, Rall = eGrep(['.*.pb.cc', '.*.pb.h'], Rall)
    Lproto = eUniq('.pb.cc$', '.proto', Rpb_all)
    Lproto = eUniq('.pb.h$', '.proto', Lproto)
    for proto in Lproto:
        Tcc = re.sub('.proto$', '.pb.cc', proto)
        Th  = re.sub('.proto$', '.pb.h', proto)
        cursor.build([Tcc, Th], 'rule_PROTOC', proto)

    # (1.3) Collect proto-text stuff and generate targets
    Rpb_text_all, Rall = eGrep(['.*.pb_text.h$', '.*.pb_text.cc$',
                          '.*.pb_text-impl.h$'], Rall)
    Lpb_text = eUniq('.pb_text.h$', '.proto', Rpb_text_all)
    Lpb_text = eUniq('.pb_text.cc$', '.proto', Lpb_text)
    Lpb_text = eUniq('.pb_text-impl.h$', '.proto', Lpb_text)
    for proto in Lpb_text:
        Tcc = re.sub('.proto$', '.pb_text.cc', proto)
        Th  = re.sub('.proto$', '.pb_text.h', proto)
        Tih = re.sub('.proto$', '.pb_text-impl.h', proto)
        cursor.build([Tcc, Th, Tih], 'rule_PROTO_TEXT', proto)

    # (1.4) Collect protobuf-python stuff
    Rpb_python, Rall = eGrep('.*_pb2.py', Rall)
    Lpb_python = eUniq('_pb2.py$', '.proto', Rpb_python)
    for proto in Lpb_python:
        Tpy = re.sub('.proto$', '_pb2.py', proto)
        cursor.build(Tpy, 'rule_PROTOC_PYTHON', proto)

    # (2.1) tf_cc_op_gen (YYY_gen_cc)
    Rcc_op_all, Rall = eGrep(['.*/cc/ops/.*.cc', '.*/cc/ops/.*.h'], Rall)
    cursor.build('tensorflow/core/ops/user_ops.cc', 'COPY', 'tensorflow/core/user_ops/fact.cc')
    # - common object
    cclist_extra = ['tensorflow/core/framework/op_gen_lib.cc',
                    'tensorflow/cc/framework/cc_op_gen.cc',
                    'tensorflow/cc/framework/cc_op_gen_main.cc', ]
    objlist = []
    for cc in cclist_extra:
        if 'tensorflow_tools_proto_text' in ag.g: break  # ungraceful...
        obj = re.sub('.cc$', '.o', cc)
        cursor.build(obj, 'rule_CXX_OBJ', cc)
        objlist.append(obj)
    # - all ops
    ops = eUniq('.cc$', '', Rcc_op_all)
    ops = eUniq('.h$', '', ops)
    ops = list(set(os.path.basename(x) for x in ops if 'internal' not in x))
    # - build elf executables and generate .cc files
    for op in ops:
        coreopcc = 'tensorflow/core/ops/' + op + '.cc'
        ccopcc   = 'tensorflow/cc/ops/'   + op + '.cc'
        ccoph    = 'tensorflow/cc/ops/'   + op + '.h'
        ccopincc = 'tensorflow/cc/ops/'   + op + '_internal.cc'
        ccopinh  = 'tensorflow/cc/ops/'   + op + '_internal.h'
        cursor.build(f'{op}_gen_cc', 'rule_CXX_EXEC', [coreopcc] + objlist,
            variables={'SHOGUN_EXTRA': '-I. -L. -ltfccopgen'})
        cursor.build([ccoph, ccopcc], 'rule_CC_OP_GEN', f'{op}_gen_cc',
                variables={'cc_op_gen_internal': '0' if op != 'sendrecv_ops' else '1'},
                implicit_outputs=[ccopinh, ccopincc])

    # (2.2) tf_python_op_gen (YYY_gen_python)
    Rpy_op_all, Rall = eGrep('^tensorflow/python.*gen_.*_ops.py$', Rall)
    cclist_extra = [
        "tensorflow/python/framework/python_op_gen.cc",
        "tensorflow/python/framework/python_op_gen_internal.cc",
        "tensorflow/python/framework/python_op_gen_main.cc" ]
    objlist = [
        'tensorflow/core/framework/op_gen_lib.o',
        ]
    for cc in cclist_extra:
        if 'tensorflow_tools_proto_text' in ag.g: break  # ungraceful...
        obj = re.sub('.cc$', '.o', cc)
        cursor.build(obj, 'rule_CXX_OBJ', cc)
        objlist.append(obj)
    for pyop in set(Rpy_op_all):
        pyop_objlist = objlist
        if 'control_flow_ops' in pyop:
            pyop_objlist.append('tensorflow/core/ops/no_op.o')
        dirname = os.path.dirname(pyop)
        basename = os.path.basename(pyop)
        op = re.sub('gen_(.*).py', '\\1', basename)
        coreopcc = 'tensorflow/core/ops/' + op + '.cc'
        cursor.build(f'{op}_gen_python', 'rule_CXX_EXEC', [coreopcc] + pyop_objlist,
            variables={'SHOGUN_EXTRA': '-I. -L. -ltfccopgen'})
        cursor.build(pyop, 'rule_PY_OP_GEN', f'{op}_gen_python')

    # (3) SWIG Wrapper
    Rpywrap, Rall = eGrep('.*pywrap_tensorflow_internal.*', Rall)
    if Rpywrap:
        pywrap = 'tensorflow/python/pywrap_tensorflow_internal.py'
        ccwrap = 'tensorflow/python/pywrap_tensorflow_internal.cc'
        cursor.build([pywrap, ccwrap], 'rule_ANYi', 'tensorflow/python/tensorflow.i',
            variables={'ANY': ' '.join('''swig -python -c++ -I.
            -module pywrap_tensorflow_internal
            -outdir tensorflow/python
            -o tensorflow/python/pywrap_tensorflow_internal.cc
            -globals "" '''.split()) })

    # (4.1) Misc source files
    Rverinfo, Rall = eGrep('tensorflow/core/util/version_info.cc', Rall)
    if Rverinfo:
        cursor.build(Rverinfo[0], 'rule_ANYio', 'debian/patches/version_info.cc',
                variables={'ANY': 'cp'})

    Rbuildinfo, Rall = eGrep('tensorflow/python/platform/build_info.py', Rall)
    if Rbuildinfo:
        cursor.build(Rbuildinfo[0], 'rule_ANYo', [],
                variables={'ANY': 'python3 tensorflow/tools/build_info/gen_build_info.py --build_config cpu --raw_generate'})

    Rfasttensorutil, Rall = eGrep('.*fast_tensor_util.*', Rall)
    if Rfasttensorutil:
        cursor.build('tensorflow/python/framework/fast_tensor_util.cpp',
                'rule_ANYi', 'tensorflow/python/framework/fast_tensor_util.pyx',
                variables={'ANY': 'cython3 -v --cplus'})
        cursor.build('tensorflow/python/framework/fast_tensor_util.cc',
                'rule_ANYio', 'tensorflow/python/framework/fast_tensor_util.cpp',
                variables={'ANY': 'cp'})

    # (.) finish
    cursor.close()
    eComplain(Rall)


def shogunPython(argv):
    '''
    Build python package layout
    '''
    ag = argparse.ArgumentParser()
    ag.add_argument('-i', type=str, required=True)
    ag.add_argument('-g', type=str, required=True)
    ag.add_argument('-o', type=str, default='pippackage.sh')
    ag.add_argument('-O', type=str, default='_pywrap_tensorflow_internal.so')
    ag.add_argument('--api', type=str, default='api_init_files_list.txt')
    ag = ag.parse_args(argv)
    print(red('Argument Dump:'))
    pprint(vars(ag))

    # Glob python files
    srclist = bazelPreprocess([l.strip() for l in open(ag.i, 'r').readlines()])
    genlist = bazelPreprocess([l.strip() for l in open(ag.g, 'r').readlines()])
    srclist.extend(genlist)
    Rapi = [l.strip().replace(';','') for l in open(ag.api, 'r').readlines()]
    srclist.extend(Rapi)
    Rpy, _ = eGrep('.*.py$', srclist)
    print('shogunPython: found', len(Rpy), 'python files')
    with open(ag.o, 'w') as f:
        f.write(f'''# Automatically generated by {__file__}
        filelist="
        ''')
        for py in Rpy:
            f.write(py+'\n')
        f.write('"\n')
        f.write('''
        for f in $filelist ; do
            if test -r $f; then
                install -Dm0644 $f $1/$f
            else
                echo $f is missing
            fi
        done
        ''')
        f.write(f'''
        install -Dm0644 {ag.O} $1/tensorflow/python/{ag.O}
        ''')
    print('=>', ag.o)


if __name__ == '__main__':

    # A graceful argparse implementation with argparse subparser requries
    # obscure code. An advantage of the current implementation is that you
    # only need to define a new shogunXXX function and it would be
    # automatically added here.
    try:
        eval(f'shogun{sys.argv[1]}')(sys.argv[2:])
    except (IndexError, NameError) as e:
        print(e, '|', 'you must specify one of the following a subcommand:')
        print([k.replace('shogun', '') for (k, v) in locals().items() if k.startswith('shogun')])
        exit(1)
