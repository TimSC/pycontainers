import bz2, struct, os, copy, gzip, time, random

class PagesFileLowLevel(object):
	def __init__(self, fi):
		createFile = False
		if isinstance(fi, str):
			createFile = not os.path.isfile(fi)
			if createFile:
				self.handle = open(fi, "w+b")
				createFile = True
			else:
				self.handle = open(fi, "r+b")
		else:
			self.handle = fi

		#self.method = "bz2 "
		self.method = "zlib"
		self.virtualCursor = 0
		self.pageStep = 1000000
		self.useTrashThreshold = 0.9

		#Index of on disk pages
		self.pageIndex = {}
		self.pageTrash = []

		#Temporary cache of decompressed pages
		self._pageCache = []
		self._metaCache = []

		#inUse, uncompSize, compSize, uncompPos, allocSize
		self.headerStruct = struct.Struct(">BQQQQ")

		self.footerStruct = struct.Struct(">Q")
		self.plainLen = 0

		if createFile:
			self._init_file_structure()
		else:
			self._refresh_page_index()

	def _init_file_structure(self):
		self.handle.seek(0)
		self.handle.write("pset")
		self.handle.write(struct.pack(">QQ", self.plainLen, self.pageStep))

	def __del__(self):
		self.flush()
		
	def flush(self):
		self.handle.seek(4)
		self.handle.write(struct.pack(">QQ", self.plainLen, self.pageStep))

	def write(self, data):

		self._pageCache = []
		self._metaCache = []

		while len(data) > 0:
			meta = self._get_page_for_index(self.virtualCursor)
			if meta is None:
				meta = {}
				meta['pagePos'] = None
				meta['compSize'] = None
				meta['uncompPos'] = self.virtualCursor - (self.virtualCursor % self.pageStep)
			 	meta['uncompSize'] = self.pageStep
				meta['method'] = self.method
				meta['allocSize'] = None
			
				pageCursor = self.virtualCursor - meta['uncompPos']
				bytesRemainingInPage = meta['uncompSize'] - pageCursor
				bytes = len(data)
				if bytes > bytesRemainingInPage:
					bytes = bytesRemainingInPage

				plain = bytearray("".join("\x00" for i in range(self.pageStep)))
				plain[pageCursor:pageCursor+bytes] = data[:bytes]
				data = data[bytes:]
				self.virtualCursor += bytes
				
				self._write_page_to_disk(meta, plain)

				if self.virtualCursor > self.plainLen:
					self.plainLen = self.virtualCursor 
				continue

			#Check if entire page written
			entire = self.virtualCursor == meta['uncompPos'] and len(data) > meta['uncompSize']
			
			if entire:
				plain = data[:meta['uncompSize']]
				data = data[meta['uncompSize']:]
				self.virtualCursor += meta['uncompSize']
			else:
				plain = bytearray(self._read_entire_page(meta))
				pageCursor = self.virtualCursor - meta['uncompPos']
				bytesRemainingInPage = meta['uncompSize'] - pageCursor
				bytes = len(data)
				if bytes > bytesRemainingInPage:
					bytes = bytesRemainingInPage
				
				plain[pageCursor:pageCursor+bytes] = data[:bytes]
				data = data[bytes:]
				self.virtualCursor += bytes

			self._write_page_to_disk(meta, plain)

			self._pageCache.append(plain)
			self._metaCache.append(meta)
			if self.virtualCursor > self.plainLen:
				self.plainLen = self.virtualCursor 

	def _refresh_page_index(self):
		self.handle.seek(0)
		if self.handle.read(4) != "pset":
			raise Exception("File format not recognised")

		self.plainLen = struct.unpack(">Q", self.handle.read(8))[0]
		self.pageStep = struct.unpack(">Q", self.handle.read(8))[0]
		self.pageIndex = {}
		self.pageTrash = []
		while True:
			meta = self._parse_header_at_cursor()
			if meta is None:
				break
			#print "meta", meta
			if meta['inUse']:
				self.pageIndex[meta['uncompPos']] = meta
			else:
				self.pageTrash.append(meta)
			self.handle.seek(meta['allocSize'], 1)
			footerData = self.handle.read(8)
			endStr = self.handle.read(4)
			if endStr != "pend":
				raise Exception("File format not recognised")

	def _parse_header_at_cursor(self):
		pagePos = self.handle.tell()
		startStr = self.handle.read(4)
		if len(startStr) == 0:
			return None
		if startStr != "page":
			raise Exception("File format not recognised")

		header = self.handle.read(self.headerStruct.size)
		inUse, uncompSize, compSize, uncompPos, allocSize = self.headerStruct.unpack(header)
		method = self.handle.read(4)
		return {'inUse': inUse, 'pagePos': pagePos, 'compSize': compSize, 'uncompPos': uncompPos,
			 'uncompSize': uncompSize, 'method': method, 'allocSize': allocSize}

	def _get_page_for_index(self, pos):

		#Seek for suitable page on disk
		expectedPageStart = pos - (pos % self.pageStep)
		if expectedPageStart in self.pageIndex:
			return self.pageIndex[expectedPageStart]

		return None

	def _read_entire_page(self, meta):

		self.handle.seek(meta['pagePos'] + self.headerStruct.size + 8)
		binData = self.handle.read(meta['compSize'])

		if meta['method'] == "bz2 ":
			import bz2
			plainData = bz2.decompress(binData)
			if len(plainData) != meta['uncompSize']:
				raise Exception("Extracted data has incorrect length")
			return plainData

		if meta['method'] == "zlib":
			import zlib
			plainData = zlib.decompress(binData)
			if len(plainData) != meta['uncompSize']:
				raise Exception("Extracted data has incorrect length")
			return plainData

		raise Exception("Not implemented")

	def read(self, bytes=None):
		self._pageCache = []
		self._metaCache = []
		if bytes is None:
			bytes = self.pageStep

		meta = self._get_page_for_index(self.virtualCursor)

		if meta is None:

			if self.virtualCursor < 0 or self.virtualCursor >= self.plainLen:
				return ""

			#Handle if we are reading within a file but no page available
			#Look for start of next real page
			nextPageStartPos = self.virtualCursor - (self.virtualCursor % self.pageStep) + self.pageStep
			while nextPageStartPos not in self.pageIndex and nextPageStartPos < bytes + self.virtualCursor:
				nextPageStartPos += self.pageStep

			bytesBeforePage = nextPageStartPos - self.virtualCursor
			#print "bytesBeforePage", bytesBeforePage
			if bytesBeforePage < bytes:
				bytes = bytesBeforePage

			bytesRemainingInFile = self.plainLen - self.virtualCursor 
			if bytes > bytesRemainingInFile:
				bytes = bytesRemainingInFile	

			self.virtualCursor += bytes
			return "".join("\x00" for i in range(bytes))

		#Read a page from disk
		plain = self._read_entire_page(meta)
		
		pageCursor = self.virtualCursor - meta['uncompPos']
		bytesRemainingInPage = len(plain) - pageCursor
		if bytes > bytesRemainingInPage:
			bytes = bytesRemainingInPage

		bytesRemainingInFile = self.plainLen - self.virtualCursor 
		if bytes > bytesRemainingInFile:
			bytes = bytesRemainingInFile		

		self.virtualCursor += bytes
		self._pageCache.append(plain)
		self._metaCache.append(meta)

		return plain[pageCursor:pageCursor+bytes]

	def tell(self):
		return self.virtualCursor

	def seek(self, pos, mode=0):
		if mode == 0:
			if pos < 0:
				raise IOError("Invalid argument")
			self.virtualCursor = pos
			return

		if mode == 1:
			if self.virtualCursor + pos < 0:
				raise IOError("Invalid argument")
			self.virtualCursor += pos
			return

		if mode == 2:
			if self.plainLen + pos < 0:
				raise IOError("Invalid argument")
			self.virtualCursor = self.plainLen + pos
			return

	def __len__(self):
		return self.plainLen

	def _write_page_to_disk(self, meta, plain):

		encodedData = None

		if meta['method'] == "bz2 ":
			import bz2
			encodedData = bz2.compress(plain)

		if meta['method'] == "zlib":
			import zlib
			encodedData = zlib.compress(str(plain))

		if encodedData == None:
			raise Exception("Not implemented compression:" + meta['method'])


		if meta['uncompPos'] not in self.pageIndex:
			self.pageIndex[meta['uncompPos']] = meta

		#Does this fit in original location
		if meta['pagePos'] is not None and len(encodedData) <= meta['compSize']:
			pass
			#print "Write page at existing position"

		else:
			if meta['pagePos'] is not None:
				#Free old location
				self._set_page_unused(meta)
				trashMeta = copy.deepcopy(meta)
				self.pageTrash.append(trashMeta)

			#Try to use a trash page
			bestTPage = None
			bestSize = None
			bestIndex = None
			for i, tpage in enumerate(self.pageTrash):
				if tpage['allocSize'] < len(encodedData):
					continue #Too small
				if tpage['allocSize'] * self.useTrashThreshold > len(encodedData):
					continue #Too too big
				if bestSize is None or tpage['allocSize'] < bestSize:
					bestSize = tpage['allocSize']
					bestTPage = tpage
					bestIndex = i

			if bestTPage is not None:
				#print "Write existing page to larger area"
				#Write to trash page
				meta['pagePos'] = bestTPage['pagePos']
				meta['allocSize'] = bestTPage['allocSize']
				del self.pageTrash[bestIndex]
			else:
				#print "Write existing page at end of file"
				#Write at end of file
				self.handle.seek(0, 2)
				meta['pagePos'] = self.handle.tell()
				meta['allocSize'] = len(encodedData)

		meta['compSize'] = len(encodedData)

		#Write to disk
		self._write_data_page(meta, plain, encodedData)

	def _set_page_unused(self, meta):

		self.handle.seek(meta['pagePos'])
		#print "Set page to unused"

		#Header
		self.handle.write("page")
		header = self.headerStruct.pack(0x00, 0, 0, 0, meta['allocSize'])
		self.handle.write(header)
		self.handle.write("free")

		#Leave footer unchanged

	def _write_data_page(self, meta, data, encoded):

		self.handle.seek(meta['pagePos'])
		#print "Write page", meta['uncompPos'], ", compressed size", len(encoded)

		#Header
		self.handle.write("page")
		header = self.headerStruct.pack(0x01, meta['uncompSize'], meta['compSize'], meta['uncompPos'], meta['allocSize'])
		self.handle.write(header)
		self.handle.write(meta['method'])

		#Copy data
		self.handle.write(encoded)

		#Footer
		self.handle.seek(meta['pagePos'] + 8 + self.headerStruct.size + meta['allocSize'])
		footer = self.footerStruct.pack(meta['allocSize'])
		self.handle.write(footer)
		self.handle.write("pend")

class PagesFile(object):

	def __init__(self, handle):

		if isinstance(handle, PagesFileLowLevel):
			self.handle = handle
		else:
			self.handle = PagesFileLowLevel(handle)
		
		self.virtualCursor = 0
		self.maxCachePages = 50

		#Index of in memory pages
		self.pagesPlain = {}
		self.pagesChanged = {}
		self.pagesLastUsed = {}

	def __del__(self):
		self.flush()
		
	def flush(self):
		for i, uncompPos in enumerate(self.pagesChanged):
			changed = self.pagesChanged[uncompPos]
			if not changed:
				continue
	
			page = self.pagesPlain[uncompPos]
			self.handle.seek(uncompPos)
			self.handle.write(page)
			self.pagesChanged[uncompPos] = False

	def _flush_old_pages(self, minToRemove=1):

		sortableList = zip(self.pagesLastUsed.values(), self.pagesLastUsed.keys())
		sortableList.sort()
		cutIndex = int(round(len(sortableList) * 0.1))
		if cutIndex < minToRemove:
			cutIndex = minToRemove
		toRemove = sortableList[:cutIndex]

		for lastUsed, ind in toRemove:
			#Write update page to disk
			if self.pagesChanged[ind]:
				self.handle.seek(ind)
				self.handle.write(self.pagesPlain[ind])

			del self.pagesPlain[ind]
			del self.pagesChanged[ind]
			del self.pagesLastUsed[ind]

	def write(self, data):

		while len(data) > 0:
			expectedPageStart = self.virtualCursor - (self.virtualCursor % self.handle.pageStep)
			expectedPageEnd = expectedPageStart + self.handle.pageStep
			localCursor = self.virtualCursor - expectedPageStart

			if expectedPageStart in self.pagesPlain:

				#Write to cache
				page = self.pagesPlain[expectedPageStart]		
				bytesRemainInPage = len(page) - localCursor
				fragmentLen = len(data)
				if fragmentLen > bytesRemainInPage:
					fragmentLen = bytesRemainInPage

				page[localCursor:localCursor+fragmentLen] = data[:fragmentLen]
				data = data[fragmentLen:]
				self.virtualCursor += fragmentLen
				self.pagesChanged[expectedPageStart] = True
				self.pagesLastUsed[expectedPageStart] = time.time()

			else:
				#Write directly to file
				bytesRemainInPage = self.handle.pageStep - localCursor
				writeFragment = data[:bytesRemainInPage]
				data = data[bytesRemainInPage:]

				self.handle.seek(self.virtualCursor)
				self.virtualCursor += len(writeFragment)
				self.handle.write(writeFragment)

				#Get local copy of cached pages
				for cp, cm in zip(self.handle._pageCache, self.handle._metaCache):
					uncompPos = cm['uncompPos']
					self.pagesPlain[uncompPos] = bytearray(cp)
					self.pagesChanged[uncompPos] = False
					self.pagesLastUsed[uncompPos] = time.time()

				#Clear old cached pages if there are too many
				if len(self.pagesPlain) > self.maxCachePages:
					self._flush_old_pages()

	def read(self, bytes=None):

		outBuffer = []
		outBufferLen = 0
		if bytes == None:
			bytes = len(self.handle) - self.virtualCursor

		while outBufferLen < bytes:
			expectedPageStart = self.virtualCursor - (self.virtualCursor % self.handle.pageStep)

			if expectedPageStart in self.pagesPlain:
				#Read from cache
				page = self.pagesPlain[expectedPageStart]
				bytesStillNeeded = bytes - outBufferLen
				localCursor = self.virtualCursor - expectedPageStart

				bytesRemainInPage = len(page) - localCursor
				if bytesStillNeeded > bytesRemainInPage:
					bytesStillNeeded = bytesRemainInPage
				
				bytesRemainInFile = len(self.handle) - self.virtualCursor
				if bytesStillNeeded > bytesRemainInFile:
					bytesStillNeeded = bytesRemainInFile

				ret = str(page[localCursor:localCursor+bytesStillNeeded])

			else:
				#Read from underlying file
				self.handle.seek(self.virtualCursor)
				ret = self.handle.read(bytes - outBufferLen)

				#Get local copy of cached pages
				for cp, cm in zip(self.handle._pageCache, self.handle._metaCache):
					uncompPos = cm['uncompPos']
					self.pagesPlain[uncompPos] = bytearray(cp)
					self.pagesChanged[uncompPos] = False
					self.pagesLastUsed[uncompPos] = time.time()

				#Clear old cached pages if there are too many
				if len(self.pagesPlain) > self.maxCachePages:
					self._flush_old_pages()

			self.virtualCursor += len(ret)
			if len(ret) == 0:
				break
			else:
				outBuffer.append(ret)
				outBufferLen += len(ret)

		#Concatenation optimisation: http://www.skymind.com/~ocrow/python_string/
		return "".join(outBuffer)

	def tell(self):
		return self.virtualCursor

	def seek(self, pos, mode=0):
		if mode == 0:
			if pos < 0:
				raise IOError("Invalid argument")
			self.virtualCursor = pos
			return

		if mode == 1:
			if self.virtualCursor + pos < 0:
				raise IOError("Invalid argument")
			self.virtualCursor += pos
			return

		if mode == 2:
			if self.plainLen + pos < 0:
				raise IOError("Invalid argument")
			self.virtualCursor = self.plainLen + pos
			return

	def __len__(self):
		return len(self.handle)

def IntegrityTest():
	try:
		os.unlink("test.pages")
	except:
		pass
	try:
		os.unlink("test.file")
	except:
		pass

	pf = PagesFile("test.pages")
	fi = open("test.file", "wb")
	for i in range(1000):
		ind = random.randint(0,100000000)
		print "Writing", i, ind
		pf.seek(ind)
		pf.write("bar243y37y3")
		fi.seek(ind)
		fi.write("bar243y37y3")

		pf.seek(0,2)
		fi.seek(0,2)
		print pf.tell(), fi.tell()

	fi.close()

	fi = open("test.file", "rb")
	pf.seek(0)
	fi.seek(0)

	while True:
		si = random.randint(0,5000000)
		test1 = pf.read(si)
		test2 = fi.read(si)
		if test1 != test2:
			print "Match error", len(test1), len(test2)
		else:
			print "Match ok", len(test1), len(test2)
		if len(test1) == 0:
			break

if __name__ == "__main__":

	pf = PagesFile("test.pages")
	if 0:
		pf.write("stuffandmorestuffxx5u4u545ugexx")
		pf.seek(0)
		print "readback", pf.read(5)

		pf.seek(999990)
		pf.write("thecatsatonthematthequickbrownfoxjumpedoverthelazybrowncow")
		pf.seek(999990)
		print "a", pf.read(20)
		print "b", pf.read(20)

		pf.seek(1500000)
		pf.write("foo42t245u54u45u")

		pf.seek(1500000)
		test = pf.read(6)
		print "'"+str(test)+"'"

		pf.seek(2500000)
		pf.write("bar")

		pf.seek(10000000)
		pf.write("bar243y37y3")

		pf.seek(9000000)
		print len(str(pf.read()))
	
		pf._flush_old_pages()
	
		pf.flush()
		print "len", len(pf)

		pf.handle._refresh_page_index()

	if 1:
		IntegrityTest()
		

