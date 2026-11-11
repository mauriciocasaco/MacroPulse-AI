using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace MacroPulse
{
    public static class DevProcessJob
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct BasicLimits
        {
            public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass, SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ExtendedLimits
        {
            public BasicLimits Basic;
            public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
            public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
            public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr attributes, IntPtr name);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimits limits, int size);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        [DllImport("kernel32.dll")]
        private static extern IntPtr GetCurrentProcess();

        [DllImport("kernel32.dll")]
        private static extern bool CloseHandle(IntPtr handle);

        public static void Attach()
        {
            const int extendedLimitInformation = 9;
            const uint killOnJobClose = 0x2000;
            var job = CreateJobObject(IntPtr.Zero, IntPtr.Zero);
            if (job == IntPtr.Zero) throw new Win32Exception();

            var limits = new ExtendedLimits();
            limits.Basic.LimitFlags = killOnJobClose;
            if (!SetInformationJobObject(job, extendedLimitInformation, ref limits, Marshal.SizeOf(limits)) ||
                !AssignProcessToJobObject(job, GetCurrentProcess()))
            {
                var error = new Win32Exception();
                CloseHandle(job);
                throw error;
            }
            // The handle is non-inheritable and remains open until this process exits.
        }
    }
}
