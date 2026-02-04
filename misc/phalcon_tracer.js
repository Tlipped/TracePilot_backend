{
    root: null,
    callStack: [],
    SIGNIFICANT_OPS: {
        "SSTORE": true, "SLOAD": true,
        "LOG0": true, "LOG1": true, "LOG2": true, "LOG3": true, "LOG4": true,
        "JUMP": true, "JUMPI": true,
        "CALL": true, "DELEGATECALL": true, "STATICCALL": true, "CALLCODE": true,
        "CREATE": true, "CREATE2": true,
        "RETURN": true, "REVERT": true, "STOP": true, "INVALID": true, "SELFDESTRUCT": true
    },

    byte2Hex: function (byte) {
        if (typeof byte !== 'number') return '00';
        return (byte < 0x10 ? '0' : '') + byte.toString(16);
    },

    array2Hex: function (arr) {
        if (!arr) return '0x0';
        var retVal = '';
        for (var i = 0; i < arr.length; i++) {
            retVal += this.byte2Hex(arr[i]);
        }
        return retVal === '' ? '0x0' : '0x' + retVal;
    },

    toHex: function(val) {
        if (val === undefined || val === null) return '0x0';
        try {
            if (typeof val === 'number') return '0x' + val.toString(16);
            if (typeof val === 'string') return val.indexOf('0x') === 0 ? val : '0x' + val;
            if (val.toString) {
                // 修复数组转逗号的问题
                if (val.length !== undefined && typeof val !== 'string') {
                    return this.array2Hex(val);
                }
                var s = val.toString(16);
                return s.indexOf('0x') === 0 ? s : '0x' + s;
            }
        } catch (e) {}
        return '0x0';
    },

    toWord: function(bigIntVal) {
        var hex = bigIntVal.toString(16);
        if (hex.length % 2 !== 0) hex = '0' + hex;
        while (hex.length < 64) { hex = '0' + hex; }
        var bytes = [];
        for (var i = 0; i < hex.length; i += 2) {
            bytes.push(parseInt(hex.substr(i, 2), 16));
        }
        return bytes;
    },


    step: function(log, db) {
        if (this.root === null) {
            var safeAddr = '0x0';
            try { safeAddr = this.array2Hex(log.contract.getAddress()); } catch(e){}

            var dummyNode = {
                type: 'ROOT',
                from: '0x0',
                to: safeAddr,
                storage_address: safeAddr,
                code_address: safeAddr,
                steps: [],
                children: []
            };
            this.root = dummyNode;
            this.callStack.push({
                node: dummyNode,
                storageAddressRaw: log.contract.getAddress(),
                storageAddressHex: safeAddr
            });
        }

        var op = log.op.toString();
        if (!this.SIGNIFICANT_OPS[op]) return;

        var frame = this.callStack[this.callStack.length - 1];
        var node = frame.node;

        var stepData = {
            pc: log.getPC(),
            op: op,
            gas: log.getGas()
        };

        if (op === "JUMP" || op === "JUMPI") {
            var stack = [];
            var sSize = log.stack.length();
            for (var i = 0; i < Math.min(sSize, 20); i++) {
                stack.push(this.toHex(log.stack.peek(i)));
            }
            stepData.stack = stack;

            var memLen = log.memory.length();
            stepData.mem_full_len = memLen;
            if (memLen > 0) {
                var captureLen = Math.min(memLen, 2048);
                stepData.mem = this.array2Hex(log.memory.slice(0, captureLen));
            }
        }
        try {
            if (op === "SLOAD") {
                var key = log.stack.peek(0);
                stepData.key = this.toHex(key);

                try {
                    if (db && db.getState) {
                        var addrBytes = frame.storageAddressRaw;
                        if (log.contract && log.contract.getAddress) {
                            addrBytes = log.contract.getAddress();
                        }
                        stepData.val = this.array2Hex(db.getState(addrBytes, this.toWord(key)));
                    } else {
                        stepData.val = "0x(no_db)";
                    }
                } catch (e) {
                    stepData.val = "0x(db_error)";
                }
            }
            // 2. SSTORE
            else if (op === "SSTORE") {
                var key = log.stack.peek(0);
                var val = log.stack.peek(1);
                stepData.key = this.toHex(key);
                stepData.val = this.toHex(val);

                try {
                    if (db && db.getState) {
                        var addrBytes = frame.storageAddressRaw; // Proxy Safe
                        if (log.contract && log.contract.getAddress) {
                            addrBytes = log.contract.getAddress();
                        }
                        stepData.old_val = this.array2Hex(db.getState(addrBytes, this.toWord(key)));
                    } else {
                        stepData.old_val = "0x(no_db)";
                    }
                } catch (e) {
                    stepData.old_val = "0x(db_error)";
                }
            }
            // 3. LOG
            else if (op.indexOf("LOG") === 0) {
                var n = parseInt(op.substring(3));
                var topics = [];
                for (var i = 0; i < n; i++) {
                    topics.push(this.toHex(log.stack.peek(2 + i)));
                }
                stepData.topics = topics;
                stepData.address = frame.storageAddressHex;

                try {
                    var off = parseInt(log.stack.peek(0).toString());
                    var len = parseInt(log.stack.peek(1).toString());
                    if (len > 0) stepData.data = this.array2Hex(log.memory.slice(off, off + len));
                    else stepData.data = '0x';
                } catch (e) {}
            }
        } catch (e) {}

        node.steps.push(stepData);
    },

    fault: function(log, db) {
        if (this.callStack.length > 0) {
            this.callStack[this.callStack.length - 1].node.error = "FAULT";
        }
    },

    enter: function(call) {
        var type = call.getType();
        var from = this.toHex(call.getFrom());
        var to = this.toHex(call.getTo());
        var toRaw = call.getTo();

        var storageAddressHex = to;
        var storageAddressRaw = toRaw;
        var codeAddressHex = to;

        if (this.callStack.length > 0) {
            var parentFrame = this.callStack[this.callStack.length - 1];

            if (type === 'DELEGATECALL') {
                storageAddressHex = parentFrame.storageAddressHex;
                storageAddressRaw = parentFrame.storageAddressRaw;
                codeAddressHex = to;
            }
            else if (type === 'CALLCODE') {
                storageAddressHex = parentFrame.storageAddressHex;
                storageAddressRaw = parentFrame.storageAddressRaw;
                codeAddressHex = to;
            }
        }

        var node = {
            type: type,
            from: from,
            to: to,
            storage_address: storageAddressHex,
            code_address: codeAddressHex,
            value: this.toHex(call.getValue()),
            gas: this.toHex(call.getGas()),
            input: this.toHex(call.getInput()),
            output: "0x",
            steps: [],
            children: []
        };

        if (this.root === null) {
            this.root = node;
        } else {
            if (this.callStack.length > 0) {
                this.callStack[this.callStack.length - 1].node.children.push(node);
            }
        }

        this.callStack.push({
            node: node,
            storageAddressHex: storageAddressHex,
            storageAddressRaw: storageAddressRaw
        });
    },

    exit: function(res) {
        if (this.callStack.length === 0) return;
        var frame = this.callStack.pop();
        var node = frame.node;
        try {
            node.gasUsed = this.toHex(res.getGasUsed());
            node.output = this.toHex(res.getOutput());
            if (res.getError()) node.error = res.getError();
        } catch (e) {}
    },

    result: function(ctx, db) {
        return {
            type: "PhalconTraceV4_ProxySafe",
            result: this.root
        };
    }
}