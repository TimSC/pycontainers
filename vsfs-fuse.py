
#FUSE interfase for very simple file system

import os, stat, errno
import vsfs

try:
	import _find_fuse_parts
except ImportError:
	pass
import fuse
from fuse import Fuse

if not hasattr(fuse, '__version__'):
	raise RuntimeError("your fuse-py doesn't know of fuse.__version__, probably it's too old.")

fuse.fuse_python_api = (0, 2)

class VsfsFuse(Fuse):

	def __init__(self, fs):
		Fuse.__init__(self)
		self.fs = fs
		self.handles = {}
		self.openCount = {}

	def getattr(self, path):
		print "getattr", path
		try:
			result = self.fs.stat(path)
			fuseStat = fuse.Stat()
			for key in result.__dict__:
				setattr(fuseStat, key, getattr(result, key))
			return fuseStat

		except OSError:
			return -errno.ENOENT
		return -errno.ENOENT

	def readdir(self, path, offset):
		#print "readdir", path, offset
		folderContent = [".", ".."]
		folderContent.extend(map(str, self.fs.listdir(path)))

		for r in folderContent:
			yield fuse.Direntry(r)

	def open(self, path, flags):
		print "open", path, flags

		mode = "r"
		if flags & os.O_RDONLY:
			mode = "r"
		if flags & os.O_WRONLY:
			mode = "w"
		if flags & os.O_RDWR:
			mode = "rw"

		if path not in self.handles:
			try:
				handle = self.fs.open(path, mode)
			except OSError:
				return -errno.ENOENT
			self.handles[path] = handle
		if path not in self.openCount:
			self.openCount[path] = 0
		self.openCount[path] += 1

	def read(self, path, size, offset):
		print "read", path, size, offset

		if path not in self.handles:
			return -errno.ENOENT
		handle = self.handles[path]	
		slen = len(handle)

		handle.seek(offset)
		return handle.read(size)

	def mknod(self, path, mode, dev):
		print "mknod", path, mode, dev
		handle = self.fs.open(path, "w")
		handle.write("stuff")
		print handle
		del handle
		return 0

	def unlink(self, path):
		print "unlink", path
		self.fs.rm(path)
		return 0

	def release(self, path, flags):
		print "release", path, flags
		if path not in self.handles:
			print "Expected path to be already open"
		self.openCount[path] -= 1
		if self.openCount[path] == 0:
			del self.openCount[path]
			del self.handles[path]
		return 0

	def flush(self, path):
		
		print "flush", path
		if path not in self.handles:
			return -errno.ENOENT
		handle = self.handles[path]	
		handle.flush()

	def utimens(self, path, accessTime, modTime):
		print "utimens", path, accessTime, modTime

	def mythread ( self ):
		print '*** mythread'
		return -errno.ENOSYS

	def chmod ( self, path, mode ):
		print '*** chmod', path, oct(mode)
		return -errno.ENOSYS

	def chown ( self, path, uid, gid ):
		print '*** chown', path, uid, gid
		return -errno.ENOSYS

	def fsync ( self, path, isFsyncFile ):
		print '*** fsync', path, isFsyncFile
		return -errno.ENOSYS

	def link ( self, targetPath, linkPath ):
		print '*** link', targetPath, linkPath
		return -errno.ENOSYS

	def mkdir ( self, path, mode ):
		print '*** mkdir', path, oct(mode)
		return -errno.ENOSYS

	def readlink ( self, path ):
		print '*** readlink', path
		return -errno.ENOSYS

	def rename ( self, oldPath, newPath ):
		print '*** rename', oldPath, newPath
		return -errno.ENOSYS

	def rmdir ( self, path ):
		print '*** rmdir', path
		return -errno.ENOSYS

	def statfs ( self ):
		print '*** statfs'
		return -errno.ENOSYS

	def symlink ( self, targetPath, linkPath ):
		print '*** symlink', targetPath, linkPath
		return -errno.ENOSYS

	def truncate ( self, path, size ):
		print '*** truncate', path, size
		return -errno.ENOSYS

	def utime ( self, path, times ):
		print '*** utime', path, times
		return -errno.ENOSYS

def main():
	fs = vsfs.Vsfs("test.vsfs")
	fi = fs.open("test.txt","w")
	fi.write("foobar\n")
	fi.close()
	del fi
	
	server = VsfsFuse(fs)
	server.parse(errex=1)
	server.main()

if __name__ == '__main__':
	main()
